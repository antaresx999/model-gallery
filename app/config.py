"""Konfiguration über Umgebungsvariablen."""
import os
import secrets
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("GALLERY_DATA", "/var/lib/model-gallery")).resolve()
SITE_NAME = os.environ.get("GALLERY_SITE_NAME", "Studio Galerie")
BASE_URL = os.environ.get("GALLERY_BASE_URL", "").rstrip("/")
COOKIE_SECURE = os.environ.get("GALLERY_COOKIE_SECURE", "0") == "1"
ADMIN_USER = os.environ.get("GALLERY_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("GALLERY_ADMIN_PASSWORD", "")
SESSION_DAYS = int(os.environ.get("GALLERY_SESSION_DAYS", "14"))

THUMB_SIZE = int(os.environ.get("GALLERY_THUMB_SIZE", "640"))
PREVIEW_SIZE = int(os.environ.get("GALLERY_PREVIEW_SIZE", "2048"))
JPEG_QUALITY = int(os.environ.get("GALLERY_JPEG_QUALITY", "85"))

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
# RAW-Formate werden unverändert als Begleitdatei zum gleichnamigen Bild gespeichert
RAW_EXT = {".dng", ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf", ".orf", ".rw2",
           ".pef", ".srw", ".x3f", ".3fr", ".fff", ".iiq", ".erf", ".mef", ".mos", ".rwl", ".raw", ".dcr", ".kdc"}

ORIGINALS_DIR = DATA_DIR / "originals"
DERIVED_DIR = DATA_DIR / "derived"
DB_PATH = DATA_DIR / "gallery.db"


def ensure_dirs():
    for d in (DATA_DIR, ORIGINALS_DIR, DERIVED_DIR):
        d.mkdir(parents=True, exist_ok=True)


def secret_key():
    env = os.environ.get("GALLERY_SECRET_KEY")
    if env:
        return env
    path = DATA_DIR / "secret.key"
    try:
        # O_EXCL: bei mehreren Workern gewinnt genau einer, alle lesen danach denselben Schlüssel
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    for _ in range(50):
        key = path.read_text().strip()
        if key:
            return key
        time.sleep(0.05)
    raise RuntimeError("secret.key ist leer")
