"""Schnittstelle für Upload-Plugins (z. B. Adobe Lightroom Classic).

Anmeldung mit API-Schlüssel im Header «Authorization: Bearer <schlüssel>». Ein Plugin-Zugang kann nur
Alben auflisten und Bilder bzw. RAW-Dateien hochladen – er sieht keine Kunden, Auswahlen oder Kommentare.
Optional ist er auf bestimmte Projekte beschränkt (Projektzugriff in der Benutzerverwaltung).
"""
import hmac
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from . import config, db, structure, uploads
from .security import api_limiter, token_hash
from .web import client_ip, get_settings

router = APIRouter(prefix="/api/plugin")

API_VERSION = 1


def plugin_user(request: Request):
    """Plugin-Zugang anhand des API-Schlüssels. Fehlversuche werden pro IP gebremst."""
    ip = client_ip(request)
    if not api_limiter.check("api:" + ip):
        raise HTTPException(429, "Zu viele Fehlversuche. Bitte später erneut versuchen.")
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "").strip()
    user = None
    if token.startswith("mgp_"):
        digest = token_hash(token)
        with db.get() as conn:
            row = conn.execute("SELECT * FROM users WHERE api_token_hash=? AND role='plugin'", (digest,)).fetchone()
        if row and hmac.compare_digest(row["api_token_hash"], digest):
            user = row
    if user is None:
        api_limiter.allow("api:" + ip)
        raise HTTPException(401, "API-Schlüssel ungültig.")
    if not user["active"]:
        raise HTTPException(403, "Dieser Plugin-Zugang ist deaktiviert.")
    with db.get() as conn:
        conn.execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (user["id"],))
    return user


def _allowed_projects(conn, user) -> Optional[set]:
    """Projekte, in die der Zugang hochladen darf. None = alle."""
    rows = conn.execute("SELECT project_id FROM project_access WHERE user_id=?", (user["id"],)).fetchall()
    return {r["project_id"] for r in rows} or None


def _albums(conn, user):
    allowed = _allowed_projects(conn, user)
    albums = conn.execute(
        """SELECT a.id, a.title, a.slug, a.project_id, a.folder_id, a.status, a.published,
                  (SELECT COUNT(*) FROM images i WHERE i.album_id=a.id) AS image_count,
                  p.title AS project_title
           FROM albums a LEFT JOIN projects p ON p.id=a.project_id
           ORDER BY p.title COLLATE NOCASE, a.title COLLATE NOCASE""").fetchall()
    folder_cache = {}
    out = []
    for a in albums:
        if allowed is not None and a["project_id"] not in allowed:
            continue
        if a["project_id"] not in folder_cache:
            folder_cache[a["project_id"]] = structure.folders_of(conn, a["project_id"]) if a["project_id"] else {}
        chain = structure.folder_chain(folder_cache[a["project_id"]], a["folder_id"])
        path = " / ".join([a["project_title"] or "Ohne Projekt"] + [f["name"] for f in chain])
        out.append({"id": a["id"], "title": a["title"], "path": path, "images": a["image_count"],
                    "status": a["status"], "published": bool(a["published"])})
    return out


def _clean(text):
    return " ".join(str(text).replace("\t", " ").split())


@router.get("/info")
def info(request: Request, format: str = "json"):
    user = plugin_user(request)
    with db.get() as conn:
        site = get_settings(conn).get("site_name") or config.SITE_NAME
    data = {"ok": True, "api": API_VERSION, "name": user["display_name"] or user["username"], "site": site,
            "image_ext": sorted(config.ALLOWED_EXT), "raw_ext": sorted(config.RAW_EXT)}
    if format == "tsv":
        return PlainTextResponse("OK\t{}\t{}\t{}\n".format(API_VERSION, _clean(data["name"]), _clean(site)))
    return data


@router.get("/alben")
def albums(request: Request, format: str = "json"):
    user = plugin_user(request)
    with db.get() as conn:
        items = _albums(conn, user)
    if format == "tsv":
        # Eine Zeile pro Album: ID, Pfad (Projekt / Ordner), Titel, Anzahl Bilder
        lines = ["{}\t{}\t{}\t{}".format(a["id"], _clean(a["path"]), _clean(a["title"]), a["images"]) for a in items]
        return PlainTextResponse("\n".join(lines) + ("\n" if lines else ""))
    return {"albums": items}


@router.post("/alben/{album_id}/bilder")
def upload(request: Request, album_id: int, file: UploadFile = File(...), ersetzen: int = 1, format: str = "json"):
    user = plugin_user(request)
    with db.get() as conn:
        album = conn.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
        allowed = _allowed_projects(conn, user)
        if not album or (allowed is not None and album["project_id"] not in allowed):
            raise HTTPException(404, "Album nicht gefunden oder kein Upload-Recht.")
    try:
        result = uploads.handle_upload(request, album_id, file.filename, file.file, replace=bool(ersetzen), actor=user)
    except uploads.UploadError as exc:
        raise HTTPException(exc.status, str(exc))
    if format == "tsv":
        return PlainTextResponse("OK\t{}\t{}\t{}\n".format(
            result["id"], "raw" if result["raw"] else "bild", "ersetzt" if result["replaced"] else "neu"))
    return result
