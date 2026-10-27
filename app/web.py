"""Gemeinsame Helfer: Templates, Sitzung, Zugriffsrechte, Einstellungen, Protokoll."""
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from . import config, images, notify
from .security import csrf_token

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Workflow-Status eines Albums (Kanban-Spalten)
STATUSES = [
    ("vorbereitung", "In Vorbereitung"),
    ("auswahl", "Auswahl läuft"),
    ("bearbeitung", "In Bearbeitung"),
    ("geliefert", "Geliefert"),
    ("archiv", "Archiv"),
]
STATUS_LABELS = dict(STATUSES)

COMMENT_KINDS = [
    ("comment", "Kommentar"),
    ("crop", "Zuschnitt"),
    ("retouch", "Retusche"),
    ("correction", "Korrektur"),
]
COMMENT_LABELS = dict(COMMENT_KINDS)

# Ereignisse, die als Benachrichtigung im Dashboard erscheinen
NOTIFY_ACTIONS = {"selection_complete", "comment"}

EVENT_LABELS = {
    "plugin_created": "Plugin-Zugang angelegt",
    "plugin_token": "Neuer API-Schlüssel erzeugt",
    "login": "Anmeldung",
    "login_failed": "Fehlgeschlagene Anmeldung",
    "upload": "Bild hochgeladen",
    "replace": "Neue Bildversion",
    "upload_raw": "RAW-Datei hochgeladen",
    "delete_image": "Bild gelöscht",
    "delete_album": "Album gelöscht",
    "delete_project": "Projekt gelöscht",
    "download_original": "Original heruntergeladen",
    "download_zip": "ZIP heruntergeladen",
    "selection_complete": "Auswahl abgeschlossen",
    "selection_reopen": "Auswahl wieder geöffnet",
    "comment": "Kommentar",
    "share_created": "Permalink erstellt",
    "share_revoked": "Permalink widerrufen",
    "share_opened": "Permalink geöffnet",
    "guest_identified": "Gast hat sich eingetragen",
    "settings": "Einstellungen geändert",
}


# --------------------------------------------------------------------------- #
# Formatierung
# --------------------------------------------------------------------------- #

def de_date(value):
    if not value:
        return ""
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return value


def local_dt(value):
    """SQLite speichert UTC – Anzeige in der Zeitzone des Servers."""
    if not value:
        return ""
    try:
        dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def media_url(img, size, wm=False):
    return "/media/{}/{}?v={}{}".format(img["id"], size, img["version"], "&wm=1" if wm else "")


templates.env.filters["de_date"] = de_date
templates.env.filters["local_dt"] = local_dt
templates.env.filters["human_size"] = images.human_size
templates.env.globals["media_url"] = media_url
templates.env.globals["STATUSES"] = STATUSES
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
templates.env.globals["COMMENT_KINDS"] = COMMENT_KINDS
templates.env.globals["EVENT_LABELS"] = EVENT_LABELS


# --------------------------------------------------------------------------- #
# Einstellungen (Branding, Wasserzeichen, Benachrichtigungen)
# --------------------------------------------------------------------------- #

SETTING_DEFAULTS = {
    "site_name": config.SITE_NAME,
    "accent_color": "#d9822b",
    "logo": "",
    "watermark_text": "",
    "watermark_opacity": "35",
    "watermark_logo": "0",
    "notify_email": "",
    "notify_selection": "1",
    "notify_comment": "0",
    "events_seen": "0",
}


def get_settings(conn):
    s = dict(SETTING_DEFAULTS)
    for row in conn.execute("SELECT key, value FROM settings"):
        s[row["key"]] = row["value"]
    return s


def save_settings(conn, values):
    conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     list(values.items()))


def request_settings(request, conn=None):
    if "_settings" not in request.scope:
        if conn is None:
            from . import db
            with db.get() as c:
                request.scope["_settings"] = get_settings(c)
        else:
            request.scope["_settings"] = get_settings(conn)
    return request.scope["_settings"]


def _darken(hex_color, factor=0.85):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#{:02x}{:02x}{:02x}".format(int(r * factor), int(g * factor), int(b * factor))


# --------------------------------------------------------------------------- #
# Rendern, Meldungen, Sitzung
# --------------------------------------------------------------------------- #

class Redirect(Exception):
    def __init__(self, url):
        self.url = url


def render(request, name, ctx=None, status=200):
    from . import db
    ctx = dict(ctx or {})
    with db.get() as conn:
        settings = request_settings(request, conn)
        user = current_user(request)
        unread = 0
        if user and user["role"] == "admin":
            unread = conn.execute("SELECT COUNT(*) FROM events WHERE notify=1 AND id>?",
                                  (int(settings.get("events_seen") or 0),)).fetchone()[0]
    accent = settings.get("accent_color") or SETTING_DEFAULTS["accent_color"]
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        accent = SETTING_DEFAULTS["accent_color"]
    ctx.setdefault("user", user)
    ctx.update({
        "csrf": csrf_token(request),
        "flash": request.session.pop("flash", None),
        "settings": settings,
        "site_name": settings.get("site_name") or config.SITE_NAME,
        "accent": accent,
        "accent_hover": _darken(accent),
        "unread": unread,
    })
    return templates.TemplateResponse(request, name, ctx, status_code=status)


def flash(request, msg, kind="ok"):
    request.session["flash"] = {"msg": msg, "kind": kind}


def current_user(request):
    """Angemeldeter Benutzer (Admin oder Kunde). Gäste melden sich nie an."""
    if "_user" in request.scope:
        return request.scope["_user"]
    from . import db
    user = None
    uid = request.session.get("uid")
    if uid:
        with db.get() as conn:
            user = conn.execute("SELECT * FROM users WHERE id=? AND active=1 AND role IN ('admin','model')",
                                (uid,)).fetchone()
        if not user:
            request.session.pop("uid", None)
    request.scope["_user"] = user
    return user


def require_user(request):
    user = current_user(request)
    if not user:
        raise Redirect("/login?next=" + request.url.path)
    return user


def require_admin(request):
    user = require_user(request)
    if user["role"] != "admin":
        raise HTTPException(403, "Nur für Administratoren.")
    return user


def client_ip(request):
    return request.client.host if request.client else "?"


def public_base(request):
    return config.BASE_URL or str(request.base_url).rstrip("/")


def safe_next(url):
    return url if url and url.startswith("/") and not url.startswith("//") else "/"


def slugify(text):
    for a, b in (("ß", "ss"), ("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:60] or "album"


def unique_slug(conn, title, exclude_id=None, table="albums"):
    assert table in ("albums", "projects")
    base = slugify(title)
    slug, n = base, 2
    while True:
        row = conn.execute("SELECT id FROM {} WHERE slug=?".format(table), (slug,)).fetchone()
        if not row or row["id"] == exclude_id:
            return slug
        slug = "{}-{}".format(base, n)
        n += 1


def today():
    return datetime.now().strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Protokoll & Benachrichtigungen
# --------------------------------------------------------------------------- #

def log_event(conn, request, action, album_id=None, image_id=None, detail="", actor=None):
    actor = actor if actor is not None else (current_user(request) if request else None)
    name = ""
    if actor:
        name = actor["display_name"] or actor["username"]
        if actor["role"] == "guest" and actor["email"]:
            name = "{} <{}>".format(name, actor["email"])
    conn.execute(
        """INSERT INTO events (action, actor_id, actor_name, album_id, image_id, detail, ip, notify)
           VALUES (?,?,?,?,?,?,?,?)""",
        (action, actor["id"] if actor else None, name, album_id, image_id, detail[:500],
         client_ip(request) if request else "", 1 if action in NOTIFY_ACTIONS else 0))
    if action in NOTIFY_ACTIONS:
        s = get_settings(conn)
        wanted = (action == "selection_complete" and s.get("notify_selection") == "1") or \
                 (action == "comment" and s.get("notify_comment") == "1")
        if wanted and s.get("notify_email"):
            album = conn.execute("SELECT title, id FROM albums WHERE id=?", (album_id,)).fetchone() if album_id else None
            subject = "{}: {}{}".format(s.get("site_name"), EVENT_LABELS.get(action, action),
                                        " – " + album["title"] if album else "")
            body = "{}\n\nVon: {}\nAlbum: {}\n{}\n\n{}/admin/alben/{}".format(
                EVENT_LABELS.get(action, action), name or "–", album["title"] if album else "–",
                detail, public_base(request) if request else config.BASE_URL, album_id or "")
            notify.send_async(s["notify_email"], subject, body)


# --------------------------------------------------------------------------- #
# Zugriffsrechte
# --------------------------------------------------------------------------- #

def album_expired(album):
    return bool(album["expires_at"]) and album["expires_at"] < today()


def share_valid(share):
    return bool(share) and not (share["expires_at"] and share["expires_at"] < today())


def user_can_see(conn, user, album):
    if not user:
        return False
    if user["role"] == "admin":
        return True
    if user["role"] != "model" or not album["published"] or album_expired(album):
        return False
    if conn.execute("SELECT 1 FROM album_access WHERE album_id=? AND user_id=?",
                    (album["id"], user["id"])).fetchone():
        return True
    return album["project_id"] is not None and conn.execute(
        "SELECT 1 FROM project_access WHERE project_id=? AND user_id=?",
        (album["project_id"], user["id"])).fetchone() is not None


def granted_share(request, conn, album, token=None):
    """Gültiger, in dieser Sitzung freigeschalteter Permalink für das Album (Album-, Projekt- oder Ordnerlink)."""
    if not album["published"] or album_expired(album):
        return None
    tokens = [token] if token else request.session.get("shares", [])
    if not tokens:
        return None
    rows = conn.execute(
        "SELECT * FROM share_links WHERE (album_id=? OR project_id=?) AND token IN ({})".format(
            ",".join("?" * len(tokens))),
        [album["id"], album["project_id"] or -1] + list(tokens)).fetchall()
    from . import structure
    valid = [r for r in rows if share_valid(r) and structure.share_covers(conn, r, album)]
    guests = request.session.get("guests", {})
    # Link bevorzugen, unter dem sich der Gast eingetragen hat, dann solche mit Download
    valid.sort(key=lambda r: (r["token"] not in guests, -r["allow_download"]))
    return valid[0] if valid else None


def guest_for(request, conn, share):
    gid = request.session.get("guests", {}).get(share["token"])
    if not gid:
        return None
    return conn.execute("SELECT * FROM users WHERE id=? AND role='guest' AND share_id=? AND active=1",
                        (gid, share["id"])).fetchone()


class Access:
    """Was der aktuelle Besucher in einem Album darf."""
    see = download = raw = watermark = select = comment = is_admin = selection_done = False
    reviewer = share = None
    needs_identity = False

    def as_dict(self):
        return {k: getattr(self, k) for k in ("see", "download", "raw", "watermark", "select", "comment",
                                              "is_admin", "selection_done", "needs_identity")}


def album_access(request, conn, album, share=None, use_user=True):
    """Rechte im Album. Auf Permalink-Seiten (use_user=False) gelten immer die Rechte des Links."""
    a = Access()
    user = current_user(request) if use_user else None
    if user and user_can_see(conn, user, album):
        a.see = True
        a.is_admin = user["role"] == "admin"
        a.watermark = bool(album["watermark"]) and not a.is_admin
        a.download = (a.is_admin or bool(album["allow_download"])) and not a.watermark
        a.reviewer = user
        a.select = bool(album["selection_enabled"]) and not a.is_admin
        a.comment = a.is_admin or bool(album["comments_enabled"])
    else:
        share = share or granted_share(request, conn, album)
        if not share:
            return a
        a.see = True
        a.share = share
        a.watermark = bool(album["watermark"] or share["watermark"])
        a.download = bool(album["allow_download"] and share["allow_download"]) and not a.watermark
        a.select = bool(album["selection_enabled"] and share["can_select"])
        a.comment = bool(album["comments_enabled"] and share["can_comment"])
        a.reviewer = guest_for(request, conn, share)
        a.needs_identity = (a.select or a.comment) and a.reviewer is None
    a.raw = a.download and (a.is_admin or bool(album["allow_raw"]))
    if a.reviewer is not None and a.select:
        a.selection_done = conn.execute("SELECT 1 FROM selection_done WHERE user_id=? AND album_id=?",
                                        (a.reviewer["id"], album["id"])).fetchone() is not None
    return a


def api_access(request, conn, album):
    """Für API-Aufrufe: Seiten eines Permalinks senden dessen Token im Header X-Share-Token mit."""
    token = request.headers.get("x-share-token")
    if token:
        share = granted_share(request, conn, album, token)
        if share:
            return album_access(request, conn, album, share=share, use_user=False)
    return album_access(request, conn, album)


def selected_ids(conn, reviewer, album_id):
    if not reviewer:
        return set()
    rows = conn.execute(
        """SELECT f.image_id FROM favorites f JOIN images i ON i.id = f.image_id
           WHERE f.user_id=? AND i.album_id=?""", (reviewer["id"], album_id)).fetchall()
    return {r["image_id"] for r in rows}


def album_images(conn, album_id):
    from . import db
    return conn.execute("SELECT * FROM images WHERE album_id=? " + db.IMAGE_ORDER, (album_id,)).fetchall()
