"""Öffentliche Seiten: Anmeldung, Galerien, Permalinks, Bildbetrachter, Auswahl, Kommentare, Downloads."""
import json
import re
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from . import db, images, structure
from .security import hash_password, login_limiter, require_csrf, verify_password
from .web import (COMMENT_LABELS, Redirect, album_access, api_access, album_expired, album_images, current_user,
                  flash, granted_share, local_dt, log_event, render, request_settings, require_user,
                  safe_next, selected_ids, share_valid, client_ip)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Anmeldung & Konto
# --------------------------------------------------------------------------- #

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    if current_user(request):
        return RedirectResponse(safe_next(next), 303)
    return render(request, "login.html", {"next": safe_next(next)})


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...),
          next: str = Form("/"), csrf: str = Form("")):
    require_csrf(request, csrf)
    if not login_limiter.allow(client_ip(request)):
        return render(request, "login.html", {"next": next, "error": "Zu viele Versuche. Bitte später erneut versuchen."}, 429)
    with db.get() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=? AND active=1 AND role IN ('admin','model')",
                            (username.strip(),)).fetchone()
        if not user or not verify_password(password, user["password_hash"]):
            log_event(conn, request, "login_failed", detail=username.strip()[:64])
            return render(request, "login.html", {"next": next, "error": "Benutzername oder Passwort falsch."}, 401)
        conn.execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (user["id"],))
        log_event(conn, request, "login", actor=user)
    keep = {k: request.session.get(k) for k in ("shares", "guests") if k in request.session}
    request.session.clear()
    request.session.update(keep)
    request.session["uid"] = user["id"]
    return RedirectResponse(safe_next(next), 303)


@router.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    require_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", 303)


@router.get("/account", response_class=HTMLResponse)
def account(request: Request):
    require_user(request)
    return render(request, "account.html")


@router.post("/account")
def account_save(request: Request, current: str = Form(...), new: str = Form(...),
                 confirm: str = Form(...), csrf: str = Form("")):
    require_csrf(request, csrf)
    user = require_user(request)
    if not verify_password(current, user["password_hash"]):
        flash(request, "Aktuelles Passwort ist falsch.", "error")
    elif len(new) < 8:
        flash(request, "Das neue Passwort muss mindestens 8 Zeichen haben.", "error")
    elif new != confirm:
        flash(request, "Die Passwörter stimmen nicht überein.", "error")
    else:
        with db.get() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new), user["id"]))
        flash(request, "Passwort geändert.")
    return RedirectResponse("/account", 303)


@router.get("/branding/logo")
def branding_logo(request: Request):
    settings = request_settings(request)
    path = images.logo_path(settings)
    if not path or not path.exists():
        raise HTTPException(404, "Kein Logo hinterlegt.")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})


# --------------------------------------------------------------------------- #
# Galerien
# --------------------------------------------------------------------------- #

@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    user = require_user(request)
    if user["role"] == "admin":
        return RedirectResponse("/admin/projekte", 303)
    with db.get() as conn:
        cards, loose = structure.project_cards(conn, user)
    if user["role"] != "admin" and len(cards) == 1 and not loose:
        return RedirectResponse("/projekt/" + cards[0]["project"]["slug"], 303)
    return render(request, "index.html", {"projects": cards, "albums": loose})


@router.get("/projekt/{slug}", response_class=HTMLResponse)
def project_view(request: Request, slug: str):
    return _project_page(request, slug, None)


@router.get("/projekt/{slug}/ordner/{folder_id}", response_class=HTMLResponse)
def folder_view(request: Request, slug: str, folder_id: int):
    return _project_page(request, slug, folder_id)


def _listing(folders, albums, folder_id, show_all=False):
    """Inhalt einer Ebene: Unterordner (nur solche mit Alben, ausser show_all) und Alben direkt darin."""
    useful = set(folders) if show_all else structure.ancestors_with_albums(folders, albums)
    here = [a for a in albums if a["folder_id"] == folder_id
            or (folder_id is None and a["folder_id"] not in folders)]
    cards = []
    for f in structure.children(folders, folder_id):
        if f["id"] not in useful:
            continue
        inside = structure.descendants(folders, f["id"])
        items = [a for a in albums if a["folder_id"] in inside]
        cover = next((a for a in items if a["cover_id"]), None)
        cards.append({"folder": f, "album_count": len(items),
                      "subfolder_count": sum(1 for x in structure.children(folders, f["id"]) if x["id"] in useful),
                      "cover_id": cover["cover_id"] if cover else None,
                      "cover_version": cover["cover_version"] if cover else None})
    return here, cards, useful


def _project_page(request, slug, folder_id):
    user = require_user(request)
    with db.get() as conn:
        project = structure.project_by_slug(conn, slug)
        folders = structure.folders_of(conn, project["id"])
        if folder_id is not None and folder_id not in folders:
            raise HTTPException(404, "Ordner nicht gefunden.")
        albums = structure.visible_albums(conn, user, "a.project_id = ?", (project["id"],))
        is_admin = user["role"] == "admin"
        if not albums and not is_admin:
            raise HTTPException(404, "Projekt nicht gefunden.")
        here, cards, useful = _listing(folders, albums, folder_id, show_all=is_admin)
        if folder_id is not None and folder_id not in useful:
            raise HTTPException(404, "Ordner nicht gefunden.")
        chain = structure.folder_chain(folders, folder_id)
    root = "/projekt/" + project["slug"]
    crumbs = [_home(user), (project["title"], root)] + [(f["name"], "{}/ordner/{}".format(root, f["id"]))
                                                               for f in chain]
    return render(request, "project.html", {
        "title": chain[-1]["name"] if chain else project["title"],
        "description": "" if chain else project["description"],
        "crumbs": crumbs[:-1], "root": root, "album_base": "/album/",
        "subfolders": cards, "albums": here,
        "manage_url": "/admin/projekte/{}{}".format(project["id"], "?ordner={}".format(folder_id) if folder_id else "")
                      if is_admin else None,
    })


def _home(user):
    """Erste Brotkrume: Kunden starten bei ihren Galerien, Admins in der Projektverwaltung."""
    if user and user["role"] == "admin":
        return ("Projekte", "/admin/projekte")
    return ("Meine Galerien", "/")


def _album_crumbs(request, conn, album):
    """Brotkrumen Galerien / Projekt / Ordner … für die Albumansicht angemeldeter Benutzer."""
    project = conn.execute("SELECT * FROM projects WHERE id=?", (album["project_id"],)).fetchone() \
        if album["project_id"] else None
    home = _home(current_user(request))
    if not project:
        return [home]
    root = "/projekt/" + project["slug"]
    chain = structure.folder_chain(structure.folders_of(conn, project["id"]), album["folder_id"])
    return [home, (project["title"], root)] + [
        (f["name"], "{}/ordner/{}".format(root, f["id"])) for f in chain]


def _share_crumbs(share, project, folders, folder_id):
    """Brotkrumen innerhalb eines Projekt-Permalinks, beginnend beim freigegebenen Projekt oder Ordner."""
    root = "/s/" + share["token"]
    scope = share["folder_id"]
    chain = structure.folder_chain(folders, folder_id)
    if scope:
        ids = [f["id"] for f in chain]
        chain = chain[ids.index(scope) + 1:] if scope in ids else []
    label = folders[scope]["name"] if scope and scope in folders else project["title"]
    return [(label, root)] + [(f["name"], "{}/ordner/{}".format(root, f["id"])) for f in chain]


def _album_page(request, conn, album, base, acc, share=None, crumbs=None):
    imgs = album_images(conn, album["id"])
    selected = selected_ids(conn, acc.reviewer, album["id"]) if acc.select else set()
    only_sel = request.query_params.get("filter") == "auswahl"
    shown = [i for i in imgs if i["id"] in selected] if only_sel else imgs
    comment_counts = {}
    if acc.comment and acc.reviewer is not None:
        q = """SELECT c.image_id, COUNT(*) AS n FROM comments c JOIN images i ON i.id=c.image_id
               WHERE i.album_id=? AND c.parent_id IS NULL"""
        args = [album["id"]]
        if not acc.is_admin:
            q += " AND c.user_id=?"
            args.append(acc.reviewer["id"])
        comment_counts = {r["image_id"]: r["n"] for r in conn.execute(q + " GROUP BY c.image_id", args)}
    done = None
    if acc.selection_done:
        done = conn.execute("SELECT * FROM selection_done WHERE user_id=? AND album_id=?",
                            (acc.reviewer["id"], album["id"])).fetchone()
    return render(request, "album.html", {
        "album": album, "images": shown, "image_count": len(imgs),
        "total_size": sum(i["size_bytes"] for i in imgs),
        "base": base, "acc": acc, "selected": selected, "only_sel": only_sel, "share": share,
        "comment_counts": comment_counts, "done": done,
        "crumbs": crumbs if crumbs is not None else (
            [("Galerie-Link" + (" · " + share["label"] if share["label"] else ""), None)] if share
            else _album_crumbs(request, conn, album)),
    })


def _photo_page(request, conn, album, image_id, base, acc, share=None):
    imgs = album_images(conn, album["id"])
    idx = next((n for n, i in enumerate(imgs) if i["id"] == image_id), None)
    if idx is None:
        raise HTTPException(404, "Bild nicht gefunden.")
    img = imgs[idx]
    selected = selected_ids(conn, acc.reviewer, album["id"]) if acc.select else set()
    return render(request, "photo.html", {
        "album": album, "images": imgs, "image": img, "index": idx,
        "prev": imgs[idx - 1] if idx > 0 else None,
        "next": imgs[idx + 1] if idx + 1 < len(imgs) else None,
        "exif": json.loads(img["exif_json"] or "{}"),
        "base": base, "acc": acc, "share": share, "selected": selected,
        "is_selected": img["id"] in selected,
    })


def _load_album_for_user(request, conn, slug):
    user = require_user(request)
    album = conn.execute("SELECT * FROM albums WHERE slug=?", (slug,)).fetchone()
    if not album:
        raise HTTPException(404, "Album nicht gefunden.")
    if album_expired(album) and user["role"] != "admin":
        raise HTTPException(410, "Diese Galerie ist abgelaufen.")
    acc = album_access(request, conn, album)
    if not acc.see or acc.share is not None:
        raise HTTPException(404, "Album nicht gefunden.")
    return album, acc


@router.get("/album/{slug}", response_class=HTMLResponse)
def album_view(request: Request, slug: str):
    with db.get() as conn:
        album, acc = _load_album_for_user(request, conn, slug)
        return _album_page(request, conn, album, "/album/" + slug, acc)


@router.get("/album/{slug}/foto/{image_id}", response_class=HTMLResponse)
def photo_view(request: Request, slug: str, image_id: int):
    with db.get() as conn:
        album, acc = _load_album_for_user(request, conn, slug)
        return _photo_page(request, conn, album, image_id, "/album/" + slug, acc)


@router.get("/album/{slug}/download.zip")
def album_zip(request: Request, slug: str, filter: str = "", raw: int = 0):
    with db.get() as conn:
        album, acc = _load_album_for_user(request, conn, slug)
        if not acc.download:
            raise HTTPException(403, "Download für dieses Album ist deaktiviert.")
        imgs = album_images(conn, album["id"])
        if filter == "auswahl":
            sel = selected_ids(conn, acc.reviewer, album["id"])
            imgs = [i for i in imgs if i["id"] in sel]
        log_event(conn, request, "download_zip", album["id"], detail="{} Bilder{}".format(len(imgs), " inkl. RAW" if raw else ""))
    return zip_response(album, imgs, suffix="-auswahl" if filter == "auswahl" else "", include_raw=bool(raw and acc.raw))


# --------------------------------------------------------------------------- #
# Permalinks & Gäste
# --------------------------------------------------------------------------- #

def _load_share(request, conn, token, need_unlock=True):
    """Permalink prüfen. Liefert (Link, Album) – bei Projekt- und Ordnerlinks ist das Album None."""
    share = conn.execute("SELECT * FROM share_links WHERE token=?", (token,)).fetchone()
    if not share_valid(share):
        raise HTTPException(404, "Dieser Link ist ungültig oder abgelaufen.")
    album = None
    if share["album_id"]:
        album = conn.execute("SELECT * FROM albums WHERE id=?", (share["album_id"],)).fetchone()
        if not album or not album["published"]:
            raise HTTPException(404, "Dieses Album ist zurzeit nicht verfügbar.")
        if album_expired(album):
            raise HTTPException(410, "Diese Galerie ist abgelaufen.")
    elif not conn.execute("SELECT 1 FROM projects WHERE id=?", (share["project_id"],)).fetchone():
        raise HTTPException(404, "Dieser Link ist ungültig oder abgelaufen.")
    granted = request.session.get("shares", [])
    if token not in granted:
        if share["password_hash"] and need_unlock:
            raise Redirect("/s/{}/entsperren".format(token))
        if not share["password_hash"]:
            request.session["shares"] = (granted + [token])[-20:]
    return share, album


def _share_title(conn, share, album):
    if album is not None:
        return album["title"]
    if share["folder_id"]:
        row = conn.execute("SELECT name FROM folders WHERE id=?", (share["folder_id"],)).fetchone()
        if row:
            return row["name"]
    row = conn.execute("SELECT title FROM projects WHERE id=?", (share["project_id"],)).fetchone()
    return row["title"] if row else "Galerie"


def _count_view(request, conn, share, album, actor=None):
    key = "viewed_" + share["token"]
    if key not in request.session:
        request.session[key] = 1
        conn.execute("UPDATE share_links SET views=views+1, last_used=datetime('now') WHERE id=?", (share["id"],))
        detail = share["label"] or share["token"][:8]
        if album is None:
            detail = "Projekt «{}» · {}".format(_share_title(conn, share, None), detail)
        log_event(conn, request, "share_opened", album["id"] if album else None, detail=detail, actor=actor)


def _share_access(request, conn, token):
    """Nur für Album-Permalinks (alte Adressen /s/{token}, /foto, /download.zip)."""
    share, album = _load_share(request, conn, token)
    if album is None:
        raise HTTPException(404, "Seite nicht gefunden.")
    return share, album, album_access(request, conn, album, share=share, use_user=False)


def _share_album(request, conn, token, slug):
    """Album innerhalb eines Projekt- oder Ordner-Permalinks."""
    share, album = _load_share(request, conn, token)
    if album is not None:
        raise HTTPException(404, "Seite nicht gefunden.")
    album = conn.execute("SELECT * FROM albums WHERE slug=?", (slug,)).fetchone()
    if not album or not structure.share_covers(conn, share, album) or not album["published"]:
        raise HTTPException(404, "Album nicht gefunden.")
    if album_expired(album):
        raise HTTPException(410, "Diese Galerie ist abgelaufen.")
    return share, album, album_access(request, conn, album, share=share, use_user=False)


def _share_project_page(request, token, folder_id):
    with db.get() as conn:
        share, album = _load_share(request, conn, token)
        if album is not None:
            if folder_id is not None:
                raise HTTPException(404, "Seite nicht gefunden.")
            acc = album_access(request, conn, album, share=share, use_user=False)
            _count_view(request, conn, share, album, acc.reviewer)
            return _album_page(request, conn, album, "/s/" + token, acc, share)
        project, folders, allowed = structure.share_scope(conn, share)
        start = share["folder_id"]
        current = folder_id if folder_id is not None else start
        if current is not None and (current not in folders or (allowed is not None and current not in allowed)):
            raise HTTPException(404, "Ordner nicht gefunden.")
        albums = structure.share_albums(conn, share)
        here, cards, useful = _listing(folders, albums, current)
        if folder_id is not None and folder_id != start and folder_id not in useful:
            raise HTTPException(404, "Ordner nicht gefunden.")
        _count_view(request, conn, share, None)
        crumbs = _share_crumbs(share, project, folders, current)
        user = current_user(request)
    title = crumbs[-1][0]
    return render(request, "project.html", {
        "title": title, "description": project["description"] if current is None else "",
        "crumbs": [("Galerie-Link" + (" · " + share["label"] if share["label"] else ""), None)] + crumbs[:-1],
        "root": "/s/" + token, "album_base": "/s/{}/album/".format(token),
        "subfolders": cards, "albums": here,
        "manage_url": "/admin/projekte/{}".format(project["id"]) if user and user["role"] == "admin" else None,
    })


@router.get("/s/{token}", response_class=HTMLResponse)
def share_view(request: Request, token: str):
    return _share_project_page(request, token, None)


@router.get("/s/{token}/ordner/{folder_id}", response_class=HTMLResponse)
def share_folder(request: Request, token: str, folder_id: int):
    return _share_project_page(request, token, folder_id)


def _share_album_crumbs(conn, share, album):
    project, folders, _ = structure.share_scope(conn, share)
    return [("Galerie-Link" + (" · " + share["label"] if share["label"] else ""), None)] + \
        _share_crumbs(share, project, folders, album["folder_id"])


@router.get("/s/{token}/album/{slug}", response_class=HTMLResponse)
def share_album_view(request: Request, token: str, slug: str):
    with db.get() as conn:
        share, album, acc = _share_album(request, conn, token, slug)
        return _album_page(request, conn, album, "/s/{}/album/{}".format(token, slug), acc, share,
                           crumbs=_share_album_crumbs(conn, share, album))


@router.get("/s/{token}/album/{slug}/foto/{image_id}", response_class=HTMLResponse)
def share_album_photo(request: Request, token: str, slug: str, image_id: int):
    with db.get() as conn:
        share, album, acc = _share_album(request, conn, token, slug)
        return _photo_page(request, conn, album, image_id, "/s/{}/album/{}".format(token, slug), acc, share)


@router.get("/s/{token}/album/{slug}/download.zip")
def share_album_zip(request: Request, token: str, slug: str, filter: str = "", raw: int = 0):
    with db.get() as conn:
        share, album, acc = _share_album(request, conn, token, slug)
        return _share_zip(request, conn, album, acc, filter, raw)


@router.get("/s/{token}/entsperren", response_class=HTMLResponse)
def share_unlock_form(request: Request, token: str):
    with db.get() as conn:
        share, album = _load_share(request, conn, token, need_unlock=False)
        title = _share_title(conn, share, album)
    return render(request, "unlock.html", {"title": title, "token": token})


@router.post("/s/{token}/entsperren")
def share_unlock(request: Request, token: str, password: str = Form(...), csrf: str = Form("")):
    require_csrf(request, csrf)
    with db.get() as conn:
        share, album = _load_share(request, conn, token, need_unlock=False)
        title = _share_title(conn, share, album)
    if not login_limiter.allow("share:" + client_ip(request)):
        return render(request, "unlock.html", {"title": title, "token": token,
                      "error": "Zu viele Versuche. Bitte später erneut versuchen."}, 429)
    if not verify_password(password, share["password_hash"] or ""):
        return render(request, "unlock.html", {"title": title, "token": token,
                      "error": "Das Passwort ist falsch."}, 401)
    request.session["shares"] = (request.session.get("shares", []) + [token])[-20:]
    return RedirectResponse("/s/" + token, 303)


@router.get("/s/{token}/foto/{image_id}", response_class=HTMLResponse)
def share_photo(request: Request, token: str, image_id: int):
    with db.get() as conn:
        share, album, acc = _share_access(request, conn, token)
        return _photo_page(request, conn, album, image_id, "/s/" + token, acc, share)


def _share_zip(request, conn, album, acc, filter, raw=0):
    if not acc.download:
        raise HTTPException(403, "Download für diesen Link ist deaktiviert.")
    imgs = album_images(conn, album["id"])
    if filter == "auswahl":
        sel = selected_ids(conn, acc.reviewer, album["id"])
        imgs = [i for i in imgs if i["id"] in sel]
    log_event(conn, request, "download_zip", album["id"], detail="{} Bilder über Link".format(len(imgs)),
              actor=acc.reviewer)
    return zip_response(album, imgs, suffix="-auswahl" if filter == "auswahl" else "", include_raw=bool(raw and acc.raw))


@router.get("/s/{token}/download.zip")
def share_zip(request: Request, token: str, filter: str = "", raw: int = 0):
    with db.get() as conn:
        share, album, acc = _share_access(request, conn, token)
        return _share_zip(request, conn, album, acc, filter, raw)


@router.post("/s/{token}/gast")
def guest_identify(request: Request, token: str, name: str = Form(...), email: str = Form(...),
                   back: str = Form(""), csrf: str = Form("")):
    require_csrf(request, csrf)
    name, email = name.strip()[:80], email.strip().lower()[:200]
    back = safe_next(back) if back.startswith("/s/" + token) else "/s/" + token
    if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        flash(request, "Bitte Namen und eine gültige E-Mail-Adresse angeben.", "error")
        return RedirectResponse(back, 303)
    if not login_limiter.allow("guest:" + client_ip(request)):
        flash(request, "Zu viele Versuche. Bitte später erneut versuchen.", "error")
        return RedirectResponse(back, 303)
    with db.get() as conn:
        share, album = _load_share(request, conn, token)
        username = "gast-{}-{}".format(share["id"], email)
        guest = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if guest:
            conn.execute("UPDATE users SET display_name=?, last_login=datetime('now') WHERE id=?", (name, guest["id"]))
        else:
            cur = conn.execute(
                """INSERT INTO users (username, display_name, email, password_hash, role, share_id, last_login)
                   VALUES (?,?,?,'!','guest',?, datetime('now'))""", (username, name, email, share["id"]))
            guest = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
            log_event(conn, request, "guest_identified", album["id"] if album else None,
                      detail=share["label"] or "", actor=guest)
    guests = dict(request.session.get("guests", {}))
    guests[token] = guest["id"]
    request.session["guests"] = guests
    flash(request, "Willkommen, {}! Du kannst jetzt Bilder auswählen und kommentieren.".format(name))
    return RedirectResponse(back, 303)


@router.post("/s/{token}/gast/abmelden")
def guest_forget(request: Request, token: str, csrf: str = Form("")):
    require_csrf(request, csrf)
    guests = dict(request.session.get("guests", {}))
    guests.pop(token, None)
    request.session["guests"] = guests
    return RedirectResponse("/s/" + token, 303)


# --------------------------------------------------------------------------- #
# Bilder ausliefern & ZIP
# --------------------------------------------------------------------------- #

@router.get("/media/{image_id}/{size}")
def media(request: Request, image_id: int, size: str, dl: int = 0):
    if size not in ("thumb", "preview", "original", "raw"):
        raise HTTPException(404, "Unbekannte Grösse.")
    with db.get() as conn:
        img = conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if not img:
            raise HTTPException(404, "Bild nicht gefunden.")
        album = conn.execute("SELECT * FROM albums WHERE id=?", (img["album_id"],)).fetchone()
        acc = album_access(request, conn, album)
        if not acc.see:
            raise HTTPException(403, "Kein Zugriff.")
        if size == "raw":
            if not acc.raw or not img["raw_stored"]:
                raise HTTPException(403 if img["raw_stored"] else 404, "RAW-Download nicht verfügbar.")
            log_event(conn, request, "download_original", album["id"], img["id"], img["raw_name"],
                      actor=acc.reviewer if acc.share else None)
        if size == "original":
            if not acc.download:
                raise HTTPException(403, "Download nicht erlaubt.")
            if dl:
                log_event(conn, request, "download_original", album["id"], img["id"], img["original_name"],
                          actor=acc.reviewer if acc.share else None)
        settings = request_settings(request, conn) if acc.watermark else None
    headers = {"Cache-Control": "private, max-age=86400"}
    if size == "raw":
        return FileResponse(images.original_path(album["id"], img["raw_stored"]), filename=img["raw_name"],
                            headers=headers, media_type="application/octet-stream")
    if size == "original":
        return FileResponse(images.original_path(album["id"], img["stored_name"]), filename=img["original_name"],
                            headers=headers, content_disposition_type="attachment" if dl else "inline")
    if acc.watermark:
        path = images.watermarked_path(album["id"], img["stored_name"], size, settings)
    else:
        path = images.derived_path(album["id"], img["stored_name"], size)
    return FileResponse(path, media_type="image/jpeg", headers=headers)


class _ZipStream:
    """Nicht-seekbarer Puffer, damit zipfile direkt in die HTTP-Antwort streamen kann."""

    def __init__(self):
        self.chunks = []

    def write(self, b):
        self.chunks.append(bytes(b))
        return len(b)

    def flush(self):
        pass

    def take(self):
        data = b"".join(self.chunks)
        self.chunks = []
        return data


def zip_response(album, imgs, suffix="", include_raw=False):
    if not imgs:
        raise HTTPException(404, "Keine Bilder zum Herunterladen.")
    items = [(images.original_path(album["id"], i["stored_name"]), i["original_name"]) for i in imgs]
    if include_raw:
        items += [(images.original_path(album["id"], i["raw_stored"]), i["raw_name"]) for i in imgs if i["raw_stored"]]
        suffix += "-raw"

    def generate():
        buf = _ZipStream()
        used = set()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for path, name in items:
                if not path.exists():
                    continue
                stem, ext = Path(name).stem, Path(name).suffix
                arc, n = name, 2
                while arc.lower() in used:
                    arc = "{}-{}{}".format(stem, n, ext)
                    n += 1
                used.add(arc.lower())
                with open(path, "rb") as src, zf.open(arc, "w", force_zip64=True) as dst:
                    while True:
                        block = src.read(1024 * 1024)
                        if not block:
                            break
                        dst.write(block)
                        yield buf.take()
                yield buf.take()
        yield buf.take()

    fname = "{}{}.zip".format(album["slug"], suffix)
    return StreamingResponse(generate(), media_type="application/zip",
                             headers={"Content-Disposition": 'attachment; filename="{}"'.format(fname)})


# --------------------------------------------------------------------------- #
# Auswahl (Favoriten) & Abschluss
# --------------------------------------------------------------------------- #

def _image_album(conn, image_id):
    img = conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
    if not img:
        raise HTTPException(404, "Bild nicht gefunden.")
    album = conn.execute("SELECT * FROM albums WHERE id=?", (img["album_id"],)).fetchone()
    return img, album


def _need_identity():
    return JSONResponse({"error": "Bitte gib zuerst deinen Namen und deine E-Mail an.", "identify": True},
                        status_code=401)


@router.post("/api/auswahl/{image_id}")
def toggle_selection(request: Request, image_id: int):
    require_csrf(request)
    with db.get() as conn:
        img, album = _image_album(conn, image_id)
        acc = api_access(request, conn, album)
        if not acc.see:
            raise HTTPException(404, "Bild nicht gefunden.")
        if not acc.select:
            raise HTTPException(403, "Die Auswahl ist hier nicht aktiviert.")
        if acc.reviewer is None:
            return _need_identity()
        if acc.selection_done:
            raise HTTPException(409, "Deine Auswahl ist bereits abgeschlossen.")
        key = (acc.reviewer["id"], image_id)
        if conn.execute("SELECT 1 FROM favorites WHERE user_id=? AND image_id=?", key).fetchone():
            conn.execute("DELETE FROM favorites WHERE user_id=? AND image_id=?", key)
            state = False
        else:
            count = len(selected_ids(conn, acc.reviewer, album["id"]))
            limit = album["selection_limit"]
            if limit and count >= limit:
                raise HTTPException(409, "Du kannst höchstens {} Bilder auswählen.".format(limit))
            conn.execute("INSERT INTO favorites (user_id, image_id) VALUES (?,?)", key)
            state = True
        count = len(selected_ids(conn, acc.reviewer, album["id"]))
    return {"selected": state, "count": count, "limit": album["selection_limit"]}


@router.post("/api/alben/{album_id}/auswahl-abschliessen")
def complete_selection(request: Request, album_id: int, payload: dict = Body(default={})):
    require_csrf(request)
    note = str(payload.get("note", ""))[:1000].strip()
    with db.get() as conn:
        album = conn.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
        if not album:
            raise HTTPException(404, "Album nicht gefunden.")
        acc = api_access(request, conn, album)
        if not acc.see or not acc.select:
            raise HTTPException(403, "Die Auswahl ist hier nicht aktiviert.")
        if acc.reviewer is None:
            return _need_identity()
        if acc.selection_done:
            raise HTTPException(409, "Die Auswahl ist bereits abgeschlossen.")
        n = len(selected_ids(conn, acc.reviewer, album_id))
        if not n:
            raise HTTPException(400, "Bitte wähle zuerst mindestens ein Bild aus.")
        conn.execute("INSERT INTO selection_done (user_id, album_id, note) VALUES (?,?,?)",
                     (acc.reviewer["id"], album_id, note))
        log_event(conn, request, "selection_complete", album_id,
                  detail="{} Bilder{}".format(n, " – " + note if note else ""), actor=acc.reviewer)
    return {"ok": True, "count": n}


# --------------------------------------------------------------------------- #
# Kommentare & Markierungen
# --------------------------------------------------------------------------- #

def _comment_json(c, acc):
    return {
        "id": c["id"], "parent_id": c["parent_id"], "kind": c["kind"],
        "kind_label": COMMENT_LABELS.get(c["kind"], c["kind"]), "body": c["body"],
        "x": c["x"], "y": c["y"], "w": c["w"], "h": c["h"],
        "author": c["author_name"], "created": local_dt(c["created_at"]),
        "resolved": bool(c["resolved_at"]),
        "mine": bool(acc.reviewer is not None and c["user_id"] == acc.reviewer["id"]),
        "from_admin": bool(c["is_admin"]),
    }


def _visible_comments(conn, image_id, acc):
    rows = conn.execute(
        """SELECT c.*, (u.role = 'admin') AS is_admin FROM comments c
           LEFT JOIN users u ON u.id = c.user_id WHERE c.image_id=? ORDER BY c.id""", (image_id,)).fetchall()
    if acc.is_admin:
        return rows
    me = acc.reviewer["id"] if acc.reviewer is not None else None
    roots = {r["id"] for r in rows if r["parent_id"] is None and r["user_id"] == me and me is not None}
    return [r for r in rows if r["id"] in roots or r["parent_id"] in roots]


def _comment_access(conn, request, image_id):
    img, album = _image_album(conn, image_id)
    acc = api_access(request, conn, album)
    if not acc.see:
        raise HTTPException(404, "Bild nicht gefunden.")
    return img, album, acc


@router.get("/api/bilder/{image_id}/kommentare")
def list_comments(request: Request, image_id: int):
    with db.get() as conn:
        img, album, acc = _comment_access(conn, request, image_id)
        rows = _visible_comments(conn, image_id, acc) if acc.comment else []
    return {"comments": [_comment_json(r, acc) for r in rows], "can_comment": acc.comment,
            "needs_identity": acc.needs_identity, "is_admin": acc.is_admin}


def _clamp(v):
    try:
        return None if v is None else max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return None


@router.post("/api/bilder/{image_id}/kommentare")
def add_comment(request: Request, image_id: int, payload: dict = Body(...)):
    require_csrf(request)
    body = str(payload.get("body", "")).strip()[:2000]
    kind = payload.get("kind") if payload.get("kind") in COMMENT_LABELS else "comment"
    parent_id = payload.get("parent_id")
    if not body:
        raise HTTPException(400, "Bitte einen Text eingeben.")
    with db.get() as conn:
        img, album, acc = _comment_access(conn, request, image_id)
        if not acc.comment:
            raise HTTPException(403, "Kommentare sind hier nicht aktiviert.")
        if acc.reviewer is None:
            return _need_identity()
        x, y, w, h = (_clamp(payload.get(k)) for k in ("x", "y", "w", "h"))
        if parent_id is not None:
            visible = {r["id"]: r for r in _visible_comments(conn, image_id, acc)}
            parent = visible.get(int(parent_id))
            if not parent or parent["parent_id"] is not None:
                raise HTTPException(404, "Kommentar nicht gefunden.")
            parent_id, kind, x, y, w, h = parent["id"], "comment", None, None, None, None
        name = acc.reviewer["display_name"] or acc.reviewer["username"]
        cur = conn.execute(
            """INSERT INTO comments (image_id, user_id, author_name, parent_id, kind, body, x, y, w, h)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (image_id, acc.reviewer["id"], name, parent_id, kind, body, x, y, w, h))
        if not acc.is_admin:
            log_event(conn, request, "comment", album["id"], image_id,
                      "{}: {}".format(img["original_name"], body[:200]), actor=acc.reviewer)
        row = conn.execute("""SELECT c.*, (u.role='admin') AS is_admin FROM comments c
                              LEFT JOIN users u ON u.id=c.user_id WHERE c.id=?""", (cur.lastrowid,)).fetchone()
    return _comment_json(row, acc)


def _own_comment(conn, request, comment_id):
    c = conn.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
    if not c:
        raise HTTPException(404, "Kommentar nicht gefunden.")
    img, album, acc = _comment_access(conn, request, c["image_id"])
    return c, acc


@router.post("/api/kommentare/{comment_id}/erledigt")
def resolve_comment(request: Request, comment_id: int):
    require_csrf(request)
    with db.get() as conn:
        c, acc = _own_comment(conn, request, comment_id)
        if not acc.is_admin:
            raise HTTPException(403, "Nur für Administratoren.")
        conn.execute("UPDATE comments SET resolved_at = CASE WHEN resolved_at IS NULL THEN datetime('now') END WHERE id=?",
                     (comment_id,))
        resolved = conn.execute("SELECT resolved_at FROM comments WHERE id=?", (comment_id,)).fetchone()[0]
    return {"resolved": bool(resolved)}


@router.post("/api/kommentare/{comment_id}/loeschen")
def delete_comment(request: Request, comment_id: int):
    require_csrf(request)
    with db.get() as conn:
        c, acc = _own_comment(conn, request, comment_id)
        if not acc.is_admin and not (acc.reviewer is not None and c["user_id"] == acc.reviewer["id"]):
            raise HTTPException(403, "Du kannst nur eigene Kommentare löschen.")
        conn.execute("DELETE FROM comments WHERE id=?", (comment_id,))
    return {"ok": True}
