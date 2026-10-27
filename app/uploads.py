"""Gemeinsame Upload-Logik für die Verwaltung und das Lightroom-Plugin."""
from pathlib import Path

from . import config, db, images
from .web import log_event


class UploadError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _same_stem(conn, album_id, name):
    """Neuestes Bild im Album mit gleichem Dateinamen (ohne Endung, Gross/klein egal)."""
    stem = Path(name).stem.lower()
    match = None
    for row in conn.execute("SELECT * FROM images WHERE album_id=? ORDER BY id", (album_id,)):
        if Path(row["original_name"]).stem.lower() == stem:
            match = row
    return match


def is_raw(name):
    return Path(name).suffix.lower() in config.RAW_EXT


def handle_upload(request, album_id, name, fileobj, replace=True, actor=None):
    """Bild (JPEG/PNG/…) oder RAW-Datei in ein Album übernehmen.

    Bilder mit gleichem Namen werden auf Wunsch als neue Version ersetzt. Eine RAW-Datei wird dem
    gleichnamigen Bild angehängt; dieses muss vorher hochgeladen worden sein.
    """
    name = Path(name or "bild.jpg").name[:200]
    if is_raw(name):
        return _handle_raw(request, album_id, name, fileobj, actor)
    with db.get() as conn:
        existing = _same_stem(conn, album_id, name) if replace else None
    try:
        meta = images.ingest(album_id, fileobj, name)
    except ValueError as exc:
        raise UploadError(str(exc))
    with db.get() as conn:
        if existing and conn.execute("SELECT 1 FROM images WHERE id=?", (existing["id"],)).fetchone():
            conn.execute(
                """UPDATE images SET original_name=:original_name, stored_name=:stored_name, width=:width,
                          height=:height, size_bytes=:size_bytes, exif_json=:exif_json,
                          taken_at=COALESCE(taken_at, :taken_at), version=version+1, updated_at=datetime('now')
                   WHERE id=:id""", dict(meta, id=existing["id"]))
            images.delete_files(album_id, existing["stored_name"])
            image_id, replaced = existing["id"], True
            log_event(conn, request, "replace", album_id, image_id, name, actor=actor)
        else:
            cur = conn.execute(
                """INSERT INTO images (album_id, original_name, stored_name, width, height, size_bytes, exif_json, taken_at)
                   VALUES (:album_id, :original_name, :stored_name, :width, :height, :size_bytes, :exif_json, :taken_at)""",
                dict(meta, album_id=album_id))
            image_id, replaced = cur.lastrowid, False
            log_event(conn, request, "upload", album_id, image_id, name, actor=actor)
        conn.execute("UPDATE albums SET updated_at=datetime('now') WHERE id=?", (album_id,))
    return {"id": image_id, "name": meta["original_name"], "replaced": replaced, "raw": False}


def _handle_raw(request, album_id, name, fileobj, actor):
    with db.get() as conn:
        target = _same_stem(conn, album_id, name)
    if not target:
        raise UploadError("Zu «{}» gibt es noch kein Bild im Album. Bitte zuerst das JPEG hochladen.".format(name), 409)
    try:
        stored, size = images.store_raw(album_id, fileobj, name)
    except ValueError as exc:
        raise UploadError(str(exc))
    with db.get() as conn:
        row = conn.execute("SELECT raw_stored FROM images WHERE id=?", (target["id"],)).fetchone()
        if not row:
            images.delete_raw(album_id, stored)
            raise UploadError("Das Bild wurde inzwischen gelöscht.", 409)
        conn.execute("UPDATE images SET raw_name=?, raw_stored=?, raw_size=?, updated_at=datetime('now') WHERE id=?",
                     (name, stored, size, target["id"]))
        log_event(conn, request, "upload_raw", album_id, target["id"], name, actor=actor)
    images.delete_raw(album_id, row["raw_stored"])
    return {"id": target["id"], "name": name, "replaced": bool(row["raw_stored"]), "raw": True}
