"""Administration: Workflow-Board, Alben, Uploads/Versionen, Auswahlen, Benutzer, Einstellungen, Protokoll."""
import csv
import io
import re
import secrets
import uuid
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from PIL import Image

from . import db, images, notify, structure, uploads
from .security import hash_password, new_api_token, require_csrf, token_hash
from .web import (STATUS_LABELS, STATUSES, album_images, flash, get_settings, log_event, public_base, render,
                  require_admin, save_settings, slugify, today, unique_slug)

router = APIRouter(prefix="/admin")
api = APIRouter(prefix="/api/admin")


def _album(conn, album_id):
    album = conn.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
    if not album:
        raise HTTPException(404, "Album nicht gefunden.")
    return album


def _back(album_id, anchor=""):
    return RedirectResponse("/admin/alben/{}{}".format(album_id, "#" + anchor if anchor else ""), 303)


# --------------------------------------------------------------------------- #
# Dashboard mit Workflow-Board und Aktivität
# --------------------------------------------------------------------------- #

DASHBOARD_SQL = """
    SELECT a.*,
      (SELECT COUNT(*) FROM images i WHERE i.album_id=a.id) AS image_count,
      COALESCE(a.cover_image_id, (SELECT i.id FROM images i WHERE i.album_id=a.id
               ORDER BY COALESCE(i.taken_at, i.created_at), i.original_name LIMIT 1)) AS cover_id,
      (SELECT COUNT(*) FROM selection_done sd WHERE sd.album_id=a.id) AS selections_done,
      (SELECT COUNT(*) FROM comments c JOIN images i ON i.id=c.image_id
         WHERE i.album_id=a.id AND c.parent_id IS NULL AND c.resolved_at IS NULL) AS open_comments,
      (SELECT p.title FROM projects p WHERE p.id=a.project_id) AS project_title
    FROM albums a ORDER BY a.shoot_date DESC, a.created_at DESC
"""


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, ansicht: str = "board"):
    require_admin(request)
    with db.get() as conn:
        albums = conn.execute(DASHBOARD_SQL).fetchall()
        stats = conn.execute("""SELECT (SELECT COUNT(*) FROM albums) AS albums,
                                       (SELECT COUNT(*) FROM images) AS images,
                                       (SELECT COALESCE(SUM(size_bytes),0) FROM images) AS bytes,
                                       (SELECT COUNT(*) FROM users WHERE role='model') AS models,
                                       (SELECT COUNT(*) FROM share_links) AS shares""").fetchone()
        seen = int(get_settings(conn).get("events_seen") or 0)
        activity = conn.execute(
            """SELECT e.*, a.title AS album_title FROM events e LEFT JOIN albums a ON a.id=e.album_id
               WHERE e.notify=1 ORDER BY e.id DESC LIMIT 12""").fetchall()
        if activity and activity[0]["id"] > seen:
            save_settings(conn, {"events_seen": str(activity[0]["id"])})
    columns = [(key, label, [a for a in albums if a["status"] == key]) for key, label in STATUSES]
    return render(request, "admin/dashboard.html", {
        "albums": albums, "columns": columns, "stats": stats, "activity": activity, "seen": seen,
        "view": "raster" if ansicht == "raster" else "board", "unread": 0,
    })


@api.post("/alben/{album_id}/status")
def set_status(request: Request, album_id: int, payload: dict = Body(...)):
    require_csrf(request)
    require_admin(request)
    status = payload.get("status")
    if status not in STATUS_LABELS:
        raise HTTPException(400, "Unbekannter Status.")
    with db.get() as conn:
        _album(conn, album_id)
        conn.execute("UPDATE albums SET status=?, updated_at=datetime('now') WHERE id=?", (status, album_id))
    return {"status": status}


# --------------------------------------------------------------------------- #
# Alben anlegen / bearbeiten
# --------------------------------------------------------------------------- #

def _album_fields(form):
    limit = form.get("selection_limit") or "0"
    status = form.get("status") or "auswahl"
    expires = form.get("expires_at") or ""
    return {
        "title": (form.get("title") or "").strip()[:120],
        "description": (form.get("description") or "").strip()[:2000],
        "shoot_date": form.get("shoot_date") or "",
        "location": (form.get("location") or "").strip()[:120],
        "published": 1 if form.get("published") else 0,
        "allow_download": 1 if form.get("allow_download") else 0,
        "allow_raw": 1 if form.get("allow_raw") else 0,
        "status": status if status in STATUS_LABELS else "auswahl",
        "expires_at": expires if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires) else None,
        "watermark": 1 if form.get("watermark") else 0,
        "selection_enabled": 1 if form.get("selection_enabled") else 0,
        "selection_limit": max(0, int(limit)) if str(limit).isdigit() else 0,
        "comments_enabled": 1 if form.get("comments_enabled") else 0,
    }


def _location(conn, form):
    """Ablageort aus dem Formular; ohne gültige Angabe kein Speichern."""
    project_id, folder_id = structure.parse_location(conn, form.get("location_ref") or "")
    return project_id, folder_id


def _form_ctx(conn, album, location=""):
    return {"album": album, "locations": structure.location_options(conn),
            "location": location or (structure.location_value(album) if album else "")}


@router.get("/alben/neu", response_class=HTMLResponse)
def album_new(request: Request, ort: str = ""):
    require_admin(request)
    with db.get() as conn:
        ctx = _form_ctx(conn, None, ort)
    if not ctx["locations"]:
        flash(request, "Lege zuerst ein Projekt an – Alben werden immer in einem Projekt abgelegt.", "error")
        return RedirectResponse("/admin/projekte", 303)
    return render(request, "admin/album_form.html", ctx)


@router.post("/alben/neu")
async def album_create(request: Request):
    form = await request.form()
    require_csrf(request, form.get("csrf", ""))
    require_admin(request)
    f = _album_fields(form)
    if not f["title"]:
        flash(request, "Bitte einen Titel angeben.", "error")
        return RedirectResponse("/admin/alben/neu", 303)
    with db.get() as conn:
        f["project_id"], f["folder_id"] = _location(conn, form)
        if not f["project_id"]:
            flash(request, "Bitte einen Ablageort (Projekt oder Ordner) wählen.", "error")
            return RedirectResponse("/admin/alben/neu", 303)
        f["slug"] = unique_slug(conn, f["title"])
        _touch_project(conn, f["project_id"])
        cols = ", ".join(f)
        cur = conn.execute("INSERT INTO albums ({}) VALUES ({})".format(cols, ", ".join("?" * len(f))), list(f.values()))
    flash(request, "Album angelegt. Jetzt Bilder hochladen.")
    return _back(cur.lastrowid)


@router.get("/alben/{album_id}/bearbeiten", response_class=HTMLResponse)
def album_edit(request: Request, album_id: int):
    require_admin(request)
    with db.get() as conn:
        album = _album(conn, album_id)
        ctx = _form_ctx(conn, album)
    return render(request, "admin/album_form.html", ctx)


@router.post("/alben/{album_id}/bearbeiten")
async def album_update(request: Request, album_id: int):
    form = await request.form()
    require_csrf(request, form.get("csrf", ""))
    require_admin(request)
    f = _album_fields(form)
    if not f["title"]:
        flash(request, "Bitte einen Titel angeben.", "error")
        return RedirectResponse("/admin/alben/{}/bearbeiten".format(album_id), 303)
    with db.get() as conn:
        album = _album(conn, album_id)
        f["project_id"], f["folder_id"] = _location(conn, form)
        if not f["project_id"]:
            flash(request, "Bitte einen Ablageort (Projekt oder Ordner) wählen.", "error")
            return RedirectResponse("/admin/alben/{}/bearbeiten".format(album_id), 303)
        _touch_project(conn, f["project_id"])
        f["slug"] = album["slug"] if album["title"] == f["title"] else unique_slug(conn, f["title"], album_id)
        sets = ", ".join("{}=?".format(k) for k in f)
        conn.execute("UPDATE albums SET {}, updated_at=datetime('now') WHERE id=?".format(sets),
                     list(f.values()) + [album_id])
    flash(request, "Album gespeichert.")
    return _back(album_id)


@router.post("/alben/{album_id}/loeschen")
def album_delete(request: Request, album_id: int, csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        album = _album(conn, album_id)
        conn.execute("DELETE FROM albums WHERE id=?", (album_id,))
        log_event(conn, request, "delete_album", detail=album["title"])
    images.delete_album_files(album_id)
    flash(request, "Album «{}» gelöscht.".format(album["title"]))
    if album["project_id"]:
        return RedirectResponse(_project_url(album["project_id"], album["folder_id"]), 303)
    return RedirectResponse("/admin", 303)


# --------------------------------------------------------------------------- #
# Projekte und Ordner
# --------------------------------------------------------------------------- #

def _touch_project(conn, project_id):
    conn.execute("UPDATE projects SET updated_at=datetime('now') WHERE id=?", (project_id,))


def _project_url(project_id, folder_id=None):
    return "/admin/projekte/{}{}".format(project_id, "?ordner={}".format(folder_id) if folder_id else "")


def _folder(conn, folder_id):
    f = conn.execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone()
    if not f:
        raise HTTPException(404, "Ordner nicht gefunden.")
    return f


def _name(value, limit=120):
    return (value or "").strip()[:limit]


@router.get("/projekte", response_class=HTMLResponse)
def projects_list(request: Request):
    require_admin(request)
    with db.get() as conn:
        projects = conn.execute("""
            SELECT p.*,
              (SELECT COUNT(*) FROM albums a WHERE a.project_id=p.id) AS album_count,
              (SELECT COUNT(*) FROM folders f WHERE f.project_id=p.id) AS folder_count,
              (SELECT COUNT(*) FROM images i JOIN albums a ON a.id=i.album_id WHERE a.project_id=p.id) AS image_count,
              (SELECT i.id FROM images i JOIN albums a ON a.id=i.album_id WHERE a.project_id=p.id
                 ORDER BY a.created_at DESC, COALESCE(i.taken_at, i.created_at) LIMIT 1) AS cover_id
            FROM projects p ORDER BY p.updated_at DESC, p.title COLLATE NOCASE""").fetchall()
    return render(request, "admin/projects.html", {"projects": projects})


@router.post("/projekte/neu")
def project_create(request: Request, title: str = Form(...), description: str = Form(""), csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    title = _name(title)
    if not title:
        flash(request, "Bitte einen Projektnamen angeben.", "error")
        return RedirectResponse("/admin/projekte", 303)
    with db.get() as conn:
        slug = unique_slug(conn, title, table="projects")
        cur = conn.execute("INSERT INTO projects (slug, title, description) VALUES (?,?,?)",
                           (slug, title, _name(description, 2000)))
    flash(request, "Projekt «{}» angelegt.".format(title))
    return RedirectResponse(_project_url(cur.lastrowid), 303)


@router.get("/projekte/{project_id}", response_class=HTMLResponse)
def project_manage(request: Request, project_id: int, ordner: Optional[int] = None):
    require_admin(request)
    with db.get() as conn:
        project = structure.project_by_id(conn, project_id)
        folders = structure.folders_of(conn, project_id)
        if ordner is not None and ordner not in folders:
            raise HTTPException(404, "Ordner nicht gefunden.")
        albums = conn.execute(DASHBOARD_SQL.replace(
            "FROM albums a ORDER BY", "FROM albums a WHERE a.project_id=? ORDER BY"), (project_id,)).fetchall()
        counts = {}
        for a in albums:
            for f in structure.folder_chain(folders, a["folder_id"]):
                counts[f["id"]] = counts.get(f["id"], 0) + 1
        here = [a for a in albums if a["folder_id"] == ordner or (ordner is None and a["folder_id"] not in folders)]
        subs = structure.children(folders, ordner)
        chain = structure.folder_chain(folders, ordner)
        # Ziele zum Verschieben: Projektebene und alle Ordner ausser dem eigenen Teilbaum
        targets = [("", project["title"] + " (oberste Ebene)")] + [
            (str(f["id"]), project["title"] + " / " + " / ".join(x["name"] for x in structure.folder_chain(folders, f["id"])))
            for _, f in structure.tree(folders)]
        blocked = {f["id"]: {str(x) for x in structure.descendants(folders, f["id"])} for f in subs}
        locations = structure.location_options(conn)
        shares = conn.execute(
            """SELECT s.*, f.name AS folder_name,
                      (SELECT COUNT(*) FROM users u WHERE u.share_id=s.id) AS guest_count
               FROM share_links s LEFT JOIN folders f ON f.id=s.folder_id
               WHERE s.project_id=? ORDER BY s.created_at DESC""", (project_id,)).fetchall()
        customers = conn.execute(
            """SELECT u.*, EXISTS(SELECT 1 FROM project_access pa WHERE pa.project_id=? AND pa.user_id=u.id) AS has_access
               FROM users u WHERE u.role='model' ORDER BY u.display_name, u.username""", (project_id,)).fetchall()
    return render(request, "admin/project.html", {
        "project": project, "tree": structure.tree(folders), "counts": counts, "folder": chain[-1] if chain else None,
        "chain": chain, "subfolders": subs, "albums": here, "targets": targets, "blocked": blocked,
        "sub_counts": {f["id"]: (len(structure.children(folders, f["id"])), counts.get(f["id"], 0)) for f in subs},
        "total_albums": len(albums), "location": structure.location_value(project_id=project_id, folder_id=ordner), "locations": locations,
        "shares": shares, "customers": customers, "today": today(), "public_base": public_base(request),
        "folder_targets": targets[1:],
    })


@router.post("/projekte/{project_id}/bearbeiten")
def project_update(request: Request, project_id: int, title: str = Form(...), description: str = Form(""),
                   csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    title = _name(title)
    with db.get() as conn:
        project = structure.project_by_id(conn, project_id)
        if title:
            slug = project["slug"] if project["title"] == title else unique_slug(conn, title, project_id, "projects")
            conn.execute("UPDATE projects SET title=?, slug=?, description=?, updated_at=datetime('now') WHERE id=?",
                         (title, slug, _name(description, 2000), project_id))
    flash(request, "Projekt gespeichert." if title else "Bitte einen Projektnamen angeben.", "ok" if title else "error")
    return RedirectResponse(_project_url(project_id), 303)


@router.post("/projekte/{project_id}/loeschen")
def project_delete(request: Request, project_id: int, csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        project = structure.project_by_id(conn, project_id)
        if conn.execute("SELECT 1 FROM albums WHERE project_id=?", (project_id,)).fetchone():
            flash(request, "Das Projekt enthält noch Alben. Verschiebe oder lösche sie zuerst.", "error")
            return RedirectResponse(_project_url(project_id), 303)
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        log_event(conn, request, "delete_project", detail=project["title"])
    flash(request, "Projekt «{}» gelöscht.".format(project["title"]))
    return RedirectResponse("/admin/projekte", 303)


@router.post("/projekte/{project_id}/zugriff")
def project_access_save(request: Request, project_id: int, user_ids: List[int] = Form([]), csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        structure.project_by_id(conn, project_id)
        conn.execute("DELETE FROM project_access WHERE project_id=?", (project_id,))
        conn.executemany("INSERT OR IGNORE INTO project_access (project_id, user_id) VALUES (?,?)",
                         [(project_id, uid) for uid in user_ids])
    flash(request, "Zugriffsrechte für das Projekt gespeichert.")
    return RedirectResponse(_project_url(project_id) + "#zugriff", 303)


@router.post("/projekte/{project_id}/links")
def project_share_create(request: Request, project_id: int, label: str = Form(""), password: str = Form(""),
                         expires_at: str = Form(""), folder_id: str = Form(""),
                         allow_download: Optional[str] = Form(None), can_select: Optional[str] = Form(None),
                         can_comment: Optional[str] = Form(None), watermark: Optional[str] = Form(None),
                         csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        project = structure.project_by_id(conn, project_id)
        fid = int(folder_id) if folder_id.isdigit() else None
        if fid is not None and _folder(conn, fid)["project_id"] != project_id:
            raise HTTPException(400, "Ungültiger Ordner.")
        conn.execute(
            """INSERT INTO share_links (token, project_id, folder_id, label, password_hash, expires_at,
                                        allow_download, can_select, can_comment, watermark)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (secrets.token_urlsafe(18), project_id, fid, label.strip()[:80],
             hash_password(password) if password else None,
             expires_at if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires_at or "") else None,
             1 if allow_download else 0, 1 if can_select else 0, 1 if can_comment else 0, 1 if watermark else 0))
        log_event(conn, request, "share_created", detail="Projekt «{}»{}".format(
            project["title"], " · " + label.strip()[:80] if label.strip() else ""))
    flash(request, "Permalink für das Projekt erstellt.")
    return RedirectResponse(_project_url(project_id) + "#links", 303)


@router.post("/projekte/{project_id}/ordner")
def folder_create(request: Request, project_id: int, name: str = Form(...), parent_id: str = Form(""),
                  csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    name = _name(name, 80)
    with db.get() as conn:
        structure.project_by_id(conn, project_id)
        parent = int(parent_id) if parent_id.isdigit() else None
        if parent is not None and _folder(conn, parent)["project_id"] != project_id:
            raise HTTPException(400, "Ungültiger Ordner.")
        if not name:
            flash(request, "Bitte einen Ordnernamen angeben.", "error")
            return RedirectResponse(_project_url(project_id, parent), 303)
        conn.execute("INSERT INTO folders (project_id, parent_id, name) VALUES (?,?,?)", (project_id, parent, name))
        _touch_project(conn, project_id)
    flash(request, "Ordner «{}» angelegt.".format(name))
    return RedirectResponse(_project_url(project_id, parent), 303)


@router.post("/ordner/{folder_id}/bearbeiten")
def folder_update(request: Request, folder_id: int, name: str = Form(...), parent_id: str = Form(""),
                  csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    name = _name(name, 80)
    with db.get() as conn:
        folder = _folder(conn, folder_id)
        folders = structure.folders_of(conn, folder["project_id"])
        parent = int(parent_id) if parent_id.isdigit() else None
        if parent is not None and (parent not in folders or parent in structure.descendants(folders, folder_id)):
            flash(request, "Ein Ordner kann nicht in sich selbst oder einen seiner Unterordner verschoben werden.", "error")
            return RedirectResponse(_project_url(folder["project_id"], folder["parent_id"]), 303)
        conn.execute("UPDATE folders SET name=?, parent_id=? WHERE id=?", (name or folder["name"], parent, folder_id))
        _touch_project(conn, folder["project_id"])
    flash(request, "Ordner gespeichert.")
    return RedirectResponse(_project_url(folder["project_id"], folder["parent_id"]), 303)


@router.post("/ordner/{folder_id}/loeschen")
def folder_delete(request: Request, folder_id: int, csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        folder = _folder(conn, folder_id)
        if conn.execute("SELECT 1 FROM folders WHERE parent_id=?", (folder_id,)).fetchone() or \
                conn.execute("SELECT 1 FROM albums WHERE folder_id=?", (folder_id,)).fetchone():
            flash(request, "Der Ordner ist nicht leer. Verschiebe oder lösche zuerst seinen Inhalt.", "error")
        else:
            conn.execute("DELETE FROM folders WHERE id=?", (folder_id,))
            flash(request, "Ordner «{}» gelöscht.".format(folder["name"]))
    return RedirectResponse(_project_url(folder["project_id"], folder["parent_id"]), 303)


@router.post("/alben/{album_id}/verschieben")
def album_move(request: Request, album_id: int, location_ref: str = Form(""), back: str = Form(""),
               csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        _album(conn, album_id)
        project_id, folder_id = structure.parse_location(conn, location_ref)
        if not project_id:
            raise HTTPException(400, "Ungültiger Ablageort.")
        conn.execute("UPDATE albums SET project_id=?, folder_id=?, updated_at=datetime('now') WHERE id=?",
                     (project_id, folder_id, album_id))
        _touch_project(conn, project_id)
    flash(request, "Album verschoben.")
    return RedirectResponse(back if back.startswith("/admin/") else _project_url(project_id, folder_id), 303)


# --------------------------------------------------------------------------- #
# Albumseite (Verwaltung)
# --------------------------------------------------------------------------- #

REVIEWERS_SQL = """
    SELECT u.*, sl.label AS share_label,
      (SELECT COUNT(*) FROM favorites f JOIN images i ON i.id=f.image_id
         WHERE f.user_id=u.id AND i.album_id=:a) AS sel_count,
      (SELECT COUNT(*) FROM comments c JOIN images i ON i.id=c.image_id
         WHERE c.user_id=u.id AND i.album_id=:a AND c.parent_id IS NULL) AS comment_count,
      sd.completed_at, sd.note
    FROM users u
    LEFT JOIN share_links sl ON sl.id = u.share_id
    LEFT JOIN selection_done sd ON sd.user_id = u.id AND sd.album_id = :a
    WHERE u.role != 'admin' AND (
          EXISTS(SELECT 1 FROM album_access aa WHERE aa.album_id=:a AND aa.user_id=u.id)
       OR EXISTS(SELECT 1 FROM project_access pa WHERE pa.project_id=:p AND pa.user_id=u.id AND u.role='model')
       OR sl.album_id = :a
       OR EXISTS(SELECT 1 FROM selection_done x WHERE x.user_id=u.id AND x.album_id=:a)
       OR EXISTS(SELECT 1 FROM comments c JOIN images i ON i.id=c.image_id WHERE c.user_id=u.id AND i.album_id=:a)
       OR EXISTS(SELECT 1 FROM favorites f JOIN images i ON i.id=f.image_id WHERE f.user_id=u.id AND i.album_id=:a))
    ORDER BY sd.completed_at IS NULL, sd.completed_at DESC, u.display_name
"""


@router.get("/alben/{album_id}", response_class=HTMLResponse)
def album_manage(request: Request, album_id: int):
    require_admin(request)
    with db.get() as conn:
        album = _album(conn, album_id)
        imgs = conn.execute(
            """SELECT i.*, (SELECT COUNT(*) FROM favorites f WHERE f.image_id = i.id) AS fav_count,
                      (SELECT COUNT(*) FROM comments c WHERE c.image_id = i.id AND c.parent_id IS NULL
                         AND c.resolved_at IS NULL) AS open_comments
               FROM images i WHERE album_id=? """ + db.IMAGE_ORDER, (album_id,)).fetchall()
        models = conn.execute(
            """SELECT u.*, EXISTS(SELECT 1 FROM album_access aa WHERE aa.album_id=? AND aa.user_id=u.id) AS has_access,
                      EXISTS(SELECT 1 FROM project_access pa WHERE pa.project_id=? AND pa.user_id=u.id) AS via_project
               FROM users u WHERE u.role='model' ORDER BY u.display_name, u.username""",
            (album_id, album["project_id"] or -1)).fetchall()
        reviewers = conn.execute(REVIEWERS_SQL, {"a": album_id, "p": album["project_id"] or -1}).fetchall()
        shares = conn.execute(
            """SELECT s.*, (SELECT COUNT(*) FROM users u WHERE u.share_id=s.id) AS guest_count
               FROM share_links s WHERE album_id=? ORDER BY created_at DESC""", (album_id,)).fetchall()
        open_comments = conn.execute(
            """SELECT c.*, i.original_name, i.version FROM comments c JOIN images i ON i.id=c.image_id
               WHERE i.album_id=? AND c.parent_id IS NULL AND c.resolved_at IS NULL
               ORDER BY c.created_at DESC LIMIT 50""", (album_id,)).fetchall()
        project = conn.execute("SELECT * FROM projects WHERE id=?", (album["project_id"],)).fetchone()
        chain = structure.folder_chain(structure.folders_of(conn, project["id"]), album["folder_id"]) if project else []
        project_links = [r for r in conn.execute("SELECT * FROM share_links WHERE project_id=?",
                                                 (album["project_id"] or -1,)).fetchall()
                         if structure.share_covers(conn, r, album)]
    return render(request, "admin/album.html", {
        "project": project, "chain": chain, "project_links": project_links,
        "album": album, "images": imgs, "models": models, "reviewers": reviewers, "shares": shares,
        "open_comments": open_comments, "public_base": public_base(request), "today": today(),
        "total_size": sum(i["size_bytes"] for i in imgs),
    })


@router.post("/alben/{album_id}/upload")
def upload(request: Request, album_id: int, file: UploadFile = File(...), ersetzen: int = 1):
    require_csrf(request)
    require_admin(request)
    with db.get() as conn:
        _album(conn, album_id)
    try:
        return uploads.handle_upload(request, album_id, file.filename, file.file, replace=bool(ersetzen))
    except uploads.UploadError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)


@router.post("/bilder/{image_id}/titelbild")
def set_cover(request: Request, image_id: int, csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        img = conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if not img:
            raise HTTPException(404, "Bild nicht gefunden.")
        conn.execute("UPDATE albums SET cover_image_id=? WHERE id=?", (image_id, img["album_id"]))
    flash(request, "Titelbild gesetzt.")
    return _back(img["album_id"], "bilder")


@router.post("/bilder/loeschen")
def delete_images(request: Request, image_ids: List[int] = Form([]), album_id: int = Form(...),
                  csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        for iid in image_ids:
            img = conn.execute("SELECT * FROM images WHERE id=? AND album_id=?", (iid, album_id)).fetchone()
            if not img:
                continue
            conn.execute("DELETE FROM images WHERE id=?", (iid,))
            conn.execute("UPDATE albums SET cover_image_id=NULL WHERE cover_image_id=?", (iid,))
            images.delete_files(album_id, img["stored_name"])
            images.delete_raw(album_id, img["raw_stored"])
            log_event(conn, request, "delete_image", album_id, None, img["original_name"])
    flash(request, "{} Bild(er) gelöscht.".format(len(image_ids)))
    return _back(album_id, "bilder")


@router.post("/alben/{album_id}/zugriff")
def album_access_save(request: Request, album_id: int, user_ids: List[int] = Form([]), csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        _album(conn, album_id)
        conn.execute("DELETE FROM album_access WHERE album_id=?", (album_id,))
        conn.executemany("INSERT OR IGNORE INTO album_access (album_id, user_id) VALUES (?,?)",
                         [(album_id, uid) for uid in user_ids])
    flash(request, "Zugriffsrechte gespeichert.")
    return _back(album_id, "zugriff")


@router.post("/alben/{album_id}/links")
def share_create(request: Request, album_id: int, label: str = Form(""), password: str = Form(""),
                 expires_at: str = Form(""), allow_download: Optional[str] = Form(None),
                 can_select: Optional[str] = Form(None), can_comment: Optional[str] = Form(None),
                 watermark: Optional[str] = Form(None), csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        _album(conn, album_id)
        conn.execute(
            """INSERT INTO share_links (token, album_id, label, password_hash, expires_at, allow_download,
                                        can_select, can_comment, watermark)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (secrets.token_urlsafe(18), album_id, label.strip()[:80],
             hash_password(password) if password else None, expires_at or None,
             1 if allow_download else 0, 1 if can_select else 0, 1 if can_comment else 0, 1 if watermark else 0))
        log_event(conn, request, "share_created", album_id, detail=label.strip()[:80])
    flash(request, "Permalink erstellt.")
    return _back(album_id, "links")


@router.post("/links/{share_id}/loeschen")
def share_delete(request: Request, share_id: int, csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        share = conn.execute("SELECT * FROM share_links WHERE id=?", (share_id,)).fetchone()
        if not share:
            raise HTTPException(404, "Link nicht gefunden.")
        conn.execute("DELETE FROM share_links WHERE id=?", (share_id,))
        log_event(conn, request, "share_revoked", share["album_id"], detail=share["label"])
    flash(request, "Permalink widerrufen.")
    if share["project_id"]:
        return RedirectResponse(_project_url(share["project_id"]) + "#links", 303)
    return _back(share["album_id"], "links")


@router.post("/kommentare/{comment_id}/erledigt")
def comment_resolve_form(request: Request, comment_id: int, csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        row = conn.execute("""SELECT i.album_id FROM comments c JOIN images i ON i.id=c.image_id
                              WHERE c.id=?""", (comment_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Kommentar nicht gefunden.")
        conn.execute("UPDATE comments SET resolved_at=datetime('now') WHERE id=?", (comment_id,))
    return _back(row["album_id"], "kommentare")


# --------------------------------------------------------------------------- #
# Auswahlen: Detail, Export für Lightroom / Capture One, wieder öffnen
# --------------------------------------------------------------------------- #

def _selection(conn, album_id, user_id):
    album = _album(conn, album_id)
    reviewer = conn.execute(
        """SELECT u.*, sl.label AS share_label, sd.completed_at, sd.note FROM users u
           LEFT JOIN share_links sl ON sl.id=u.share_id
           LEFT JOIN selection_done sd ON sd.user_id=u.id AND sd.album_id=?
           WHERE u.id=?""", (album_id, user_id)).fetchone()
    if not reviewer:
        raise HTTPException(404, "Person nicht gefunden.")
    imgs = conn.execute(
        """SELECT i.* FROM images i JOIN favorites f ON f.image_id=i.id
           WHERE f.user_id=? AND i.album_id=? """ + db.IMAGE_ORDER, (user_id, album_id)).fetchall()
    return album, reviewer, imgs


@router.get("/alben/{album_id}/auswahl/{user_id}", response_class=HTMLResponse)
def selection_detail(request: Request, album_id: int, user_id: int):
    require_admin(request)
    with db.get() as conn:
        album, reviewer, imgs = _selection(conn, album_id, user_id)
        comments = conn.execute(
            """SELECT c.*, i.original_name, i.version FROM comments c JOIN images i ON i.id=c.image_id
               WHERE c.user_id=? AND i.album_id=? AND c.parent_id IS NULL ORDER BY i.original_name, c.id""",
            (user_id, album_id)).fetchall()
    stems = [Path(i["original_name"]).stem for i in imgs]
    return render(request, "admin/selection.html", {
        "album": album, "reviewer": reviewer, "images": imgs, "comments": comments,
        "lightroom": " ".join(stems), "comma": ", ".join(i["original_name"] for i in imgs),
    })


@router.get("/alben/{album_id}/auswahl/{user_id}/export.csv")
def selection_csv(request: Request, album_id: int, user_id: int):
    require_admin(request)
    with db.get() as conn:
        album, reviewer, imgs = _selection(conn, album_id, user_id)
        notes = {}
        for c in conn.execute("""SELECT c.image_id, c.kind, c.body FROM comments c JOIN images i ON i.id=c.image_id
                                 WHERE c.user_id=? AND i.album_id=? AND c.parent_id IS NULL""", (user_id, album_id)):
            notes.setdefault(c["image_id"], []).append("[{}] {}".format(c["kind"], c["body"]))
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Dateiname", "Name ohne Endung", "Version", "Kommentare"])
    for i in imgs:
        w.writerow([i["original_name"], Path(i["original_name"]).stem, i["version"], " | ".join(notes.get(i["id"], []))])
    fname = "auswahl-{}-{}.csv".format(album["slug"], re.sub(r"[^a-z0-9]+", "-", (reviewer["display_name"] or "").lower()))
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="{}"'.format(fname)})


@router.post("/alben/{album_id}/auswahl/{user_id}/oeffnen")
def selection_reopen(request: Request, album_id: int, user_id: int, csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        album, reviewer, _ = _selection(conn, album_id, user_id)
        conn.execute("DELETE FROM selection_done WHERE user_id=? AND album_id=?", (user_id, album_id))
        log_event(conn, request, "selection_reopen", album_id, detail=reviewer["display_name"])
    flash(request, "Auswahl von {} ist wieder offen.".format(reviewer["display_name"]))
    return RedirectResponse("/admin/alben/{}/auswahl/{}".format(album_id, user_id), 303)


# --------------------------------------------------------------------------- #
# Benutzer
# --------------------------------------------------------------------------- #

@router.get("/benutzer", response_class=HTMLResponse)
def users(request: Request):
    require_admin(request)
    with db.get() as conn:
        rows = conn.execute(
            """SELECT u.*, (SELECT COUNT(*) FROM album_access aa WHERE aa.user_id=u.id) AS album_count,
                      (SELECT COUNT(*) FROM project_access pa WHERE pa.user_id=u.id) AS project_count
               FROM users u WHERE u.role != 'guest'
               ORDER BY CASE u.role WHEN 'admin' THEN 0 WHEN 'model' THEN 1 ELSE 2 END, u.display_name, u.username""").fetchall()
        guests = conn.execute(
            """SELECT u.*, sl.label AS share_label, a.title AS album_title, a.id AS album_id,
                      p.title AS project_title, p.id AS project_id FROM users u
               JOIN share_links sl ON sl.id=u.share_id LEFT JOIN albums a ON a.id=sl.album_id
               LEFT JOIN projects p ON p.id=sl.project_id
               WHERE u.role='guest' ORDER BY u.last_login DESC LIMIT 100""").fetchall()
    return render(request, "admin/users.html", {"users": rows, "guests": guests})


@router.post("/benutzer/neu")
def user_create(request: Request, username: str = Form(""), display_name: str = Form(""),
                email: str = Form(""), password: str = Form(""), role: str = Form("model"),
                back: str = Form(""), csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    username = username.strip()
    if not username and role == "plugin" and display_name.strip():
        # Plugin-Zugänge brauchen keinen sprechenden Benutzernamen – aus der Bezeichnung ableiten
        base = "plugin-" + slugify(display_name)[:40]
        with db.get() as conn:
            username, n = base, 2
            while conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                username, n = "{}-{}".format(base, n), n + 1
    if not re.fullmatch(r"[A-Za-z0-9._@-]{2,64}", username) or username.lower().startswith("gast-"):
        flash(request, "Benutzername: 2–64 Zeichen, nur Buchstaben, Ziffern und . _ @ - (nicht «gast-…»)", "error")
        return RedirectResponse("/admin/benutzer", 303)
    generated = not password
    password = password or secrets.token_urlsafe(9)
    role = role if role in ("admin", "model", "plugin") else "model"
    with db.get() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            flash(request, "Benutzername ist bereits vergeben.", "error")
            return RedirectResponse("/admin/benutzer", 303)
        if role == "plugin":
            # Plugin-Zugänge haben kein Passwort, nur einen API-Schlüssel
            token = new_api_token()
            cur = conn.execute(
                """INSERT INTO users (username, display_name, email, password_hash, role, api_token_hash)
                   VALUES (?,?,'','!','plugin',?)""", (username, display_name.strip() or username, token_hash(token)))
            log_event(conn, request, "plugin_created", detail=username)
            request.session["new_token"] = {"uid": cur.lastrowid, "token": token}
            flash(request, "Plugin-Zugang angelegt. Kopiere jetzt den API-Schlüssel – er wird nur einmal angezeigt.")
            if back == "einstellungen":
                return RedirectResponse("/admin/einstellungen#lightroom", 303)
            return RedirectResponse("/admin/benutzer/{}".format(cur.lastrowid), 303)
        cur = conn.execute(
            "INSERT INTO users (username, display_name, email, password_hash, role) VALUES (?,?,?,?,?)",
            (username, display_name.strip() or username, email.strip(), hash_password(password), role))
    if generated:
        flash(request, "Benutzer angelegt. Generiertes Passwort: {}  (jetzt notieren!)".format(password))
    else:
        flash(request, "Benutzer angelegt.")
    return RedirectResponse("/admin/benutzer/{}".format(cur.lastrowid), 303)


def _target(conn, user_id):
    target = conn.execute("SELECT * FROM users WHERE id=? AND role != 'guest'", (user_id,)).fetchone()
    if not target:
        raise HTTPException(404, "Benutzer nicht gefunden.")
    return target


@router.get("/benutzer/{user_id}", response_class=HTMLResponse)
def user_detail(request: Request, user_id: int):
    require_admin(request)
    with db.get() as conn:
        target = _target(conn, user_id)
        albums = conn.execute(
            """SELECT a.*, EXISTS(SELECT 1 FROM album_access aa WHERE aa.album_id=a.id AND aa.user_id=?) AS has_access
               FROM albums a ORDER BY a.shoot_date DESC, a.created_at DESC""", (user_id,)).fetchall()
        projects = conn.execute(
            """SELECT p.*, EXISTS(SELECT 1 FROM project_access pa WHERE pa.project_id=p.id AND pa.user_id=?) AS has_access
               FROM projects p ORDER BY p.title COLLATE NOCASE""", (user_id,)).fetchall()
    new_token = request.session.pop("new_token", None)
    token = new_token["token"] if new_token and new_token.get("uid") == user_id else None
    return render(request, "admin/user.html", {"target": target, "albums": albums, "projects": projects,
                                               "new_token": token, "public_base": public_base(request)})


@router.post("/benutzer/{user_id}")
def user_update(request: Request, user_id: int, display_name: str = Form(""), email: str = Form(""),
                role: str = Form("model"), active: Optional[str] = Form(None), password: str = Form(""),
                album_ids: List[int] = Form([]), project_ids: List[int] = Form([]), csrf: str = Form("")):
    require_csrf(request, csrf)
    me = require_admin(request)
    role = "admin" if role == "admin" else "model"
    with db.get() as conn:
        target = _target(conn, user_id)
        if target["role"] == "plugin":
            role, email, password, album_ids = "plugin", "", "", []
        if target["id"] == me["id"]:
            role, active = "admin", "1"  # sich nicht selbst aussperren
        conn.execute("UPDATE users SET display_name=?, email=?, role=?, active=? WHERE id=?",
                     (display_name.strip() or target["username"], email.strip(), role, 1 if active else 0, user_id))
        if password and role != "plugin":
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user_id))
        conn.execute("DELETE FROM album_access WHERE user_id=?", (user_id,))
        conn.executemany("INSERT OR IGNORE INTO album_access (album_id, user_id) VALUES (?,?)",
                         [(aid, user_id) for aid in album_ids])
        conn.execute("DELETE FROM project_access WHERE user_id=?", (user_id,))
        conn.executemany("INSERT OR IGNORE INTO project_access (project_id, user_id) VALUES (?,?)",
                         [(pid, user_id) for pid in project_ids])
    flash(request, "Benutzer gespeichert." + (" Neues Passwort gesetzt." if password else ""))
    return RedirectResponse("/admin/benutzer/{}".format(user_id), 303)


@router.post("/benutzer/{user_id}/schluessel")
def plugin_new_token(request: Request, user_id: int, back: str = Form(""), csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        target = _target(conn, user_id)
        if target["role"] != "plugin":
            raise HTTPException(400, "Nur Plugin-Zugänge haben einen API-Schlüssel.")
        token = new_api_token()
        conn.execute("UPDATE users SET api_token_hash=? WHERE id=?", (token_hash(token), user_id))
        log_event(conn, request, "plugin_token", detail=target["username"])
    request.session["new_token"] = {"uid": user_id, "token": token}
    flash(request, "Neuer API-Schlüssel erzeugt. Der alte funktioniert ab sofort nicht mehr.")
    if back == "einstellungen":
        return RedirectResponse("/admin/einstellungen#lightroom", 303)
    return RedirectResponse("/admin/benutzer/{}".format(user_id), 303)


LIGHTROOM_PLUGIN = Path(__file__).parent / "lightroom" / "ModelGallery.lrplugin"


@router.get("/plugin/lightroom.zip")
def plugin_download(request: Request):
    require_admin(request)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(LIGHTROOM_PLUGIN.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                zf.write(p, "ModelGallery.lrplugin/" + p.relative_to(LIGHTROOM_PLUGIN).as_posix())
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="ModelGallery-Lightroom-Plugin.zip"'})


@router.post("/benutzer/{user_id}/loeschen")
def user_delete(request: Request, user_id: int, csrf: str = Form("")):
    require_csrf(request, csrf)
    me = require_admin(request)
    if user_id == me["id"]:
        flash(request, "Das eigene Konto kann nicht gelöscht werden.", "error")
        return RedirectResponse("/admin/benutzer/{}".format(user_id), 303)
    with db.get() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    flash(request, "Benutzer gelöscht.")
    return RedirectResponse("/admin/benutzer", 303)


# --------------------------------------------------------------------------- #
# Einstellungen: Branding, Wasserzeichen, Benachrichtigungen
# --------------------------------------------------------------------------- #

@router.get("/einstellungen", response_class=HTMLResponse)
def settings_page(request: Request):
    require_admin(request)
    with db.get() as conn:
        plugins = conn.execute(
            """SELECT u.*, (SELECT COUNT(*) FROM project_access pa WHERE pa.user_id=u.id) AS project_count
               FROM users u WHERE u.role='plugin' ORDER BY u.display_name, u.username""").fetchall()
    new_token = request.session.pop("new_token", None)
    token_user = next((p for p in plugins if new_token and p["id"] == new_token.get("uid")), None)
    return render(request, "admin/settings.html", {
        "smtp_ok": notify.configured(), "smtp_host": notify.SMTP_HOST, "plugins": plugins,
        "new_token": new_token["token"] if token_user else None, "token_user": token_user,
        "public_base": public_base(request)})


@router.post("/einstellungen")
async def settings_save(request: Request):
    form = await request.form()
    require_csrf(request, form.get("csrf", ""))
    require_admin(request)
    accent = form.get("accent_color") or "#d9822b"
    values = {
        "site_name": (form.get("site_name") or "").strip()[:80],
        "accent_color": accent if re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else "#d9822b",
        "watermark_text": (form.get("watermark_text") or "").strip()[:80],
        "watermark_opacity": str(max(5, min(90, int(form.get("watermark_opacity") or 35)))),
        "watermark_logo": "1" if form.get("watermark_logo") else "0",
        "notify_email": (form.get("notify_email") or "").strip()[:200],
        "notify_selection": "1" if form.get("notify_selection") else "0",
        "notify_comment": "1" if form.get("notify_comment") else "0",
    }
    logo = form.get("logo_file")
    with db.get() as conn:
        old = get_settings(conn).get("logo", "")
        if form.get("logo_remove") and old:
            (images.LOGO_DIR / old).unlink(missing_ok=True)
            values["logo"] = ""
        if logo is not None and getattr(logo, "filename", ""):
            try:
                with Image.open(logo.file) as im:
                    im.load()
                    im = im.convert("RGBA")
                    im.thumbnail((800, 800), Image.LANCZOS)
                    images.LOGO_DIR.mkdir(parents=True, exist_ok=True)
                    name = "logo-{}.png".format(uuid.uuid4().hex[:8])
                    im.save(images.LOGO_DIR / name, "PNG")
                if old:
                    (images.LOGO_DIR / old).unlink(missing_ok=True)
                values["logo"] = name
            except Exception:
                flash(request, "Das Logo konnte nicht gelesen werden (PNG, JPEG oder WebP).", "error")
                return RedirectResponse("/admin/einstellungen", 303)
        save_settings(conn, values)
        log_event(conn, request, "settings")
    flash(request, "Einstellungen gespeichert.")
    return RedirectResponse("/admin/einstellungen", 303)


@router.post("/einstellungen/testmail")
def settings_testmail(request: Request, csrf: str = Form("")):
    require_csrf(request, csrf)
    require_admin(request)
    with db.get() as conn:
        s = get_settings(conn)
    if not s.get("notify_email"):
        flash(request, "Bitte zuerst eine Empfängeradresse speichern.", "error")
    else:
        try:
            notify.send(s["notify_email"], "{}: Test-E-Mail".format(s["site_name"]),
                        "Die E-Mail-Benachrichtigungen funktionieren.")
            flash(request, "Test-E-Mail an {} gesendet.".format(s["notify_email"]))
        except Exception as exc:
            flash(request, "Versand fehlgeschlagen: {}".format(exc), "error")
    return RedirectResponse("/admin/einstellungen", 303)


# --------------------------------------------------------------------------- #
# Protokoll
# --------------------------------------------------------------------------- #

@router.get("/protokoll", response_class=HTMLResponse)
def event_log(request: Request, aktion: str = "", seite: int = 1):
    require_admin(request)
    per_page = 100
    seite = max(1, seite)
    where, args = "", []
    if aktion:
        where, args = "WHERE e.action=?", [aktion]
    with db.get() as conn:
        total = conn.execute("SELECT COUNT(*) FROM events e " + where, args).fetchone()[0]
        rows = conn.execute(
            """SELECT e.*, a.title AS album_title FROM events e LEFT JOIN albums a ON a.id=e.album_id
               {} ORDER BY e.id DESC LIMIT ? OFFSET ?""".format(where), args + [per_page, (seite - 1) * per_page]).fetchall()
        actions = [r[0] for r in conn.execute("SELECT DISTINCT action FROM events ORDER BY action")]
    return render(request, "admin/log.html", {
        "events": rows, "actions": actions, "aktion": aktion, "seite": seite,
        "pages": max(1, (total + per_page - 1) // per_page), "total": total,
    })
