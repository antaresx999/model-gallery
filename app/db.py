"""SQLite-Datenbank: Schema, Migrationen und Hilfsfunktionen."""
import sqlite3
import time
from contextlib import contextmanager

from . import config

# Version 1: Grundschema
V1 = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'model' CHECK (role IN ('admin','model')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_login    TEXT
);
CREATE TABLE IF NOT EXISTS albums (
    id             INTEGER PRIMARY KEY,
    slug           TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    shoot_date     TEXT NOT NULL DEFAULT '',
    location       TEXT NOT NULL DEFAULT '',
    cover_image_id INTEGER,
    published      INTEGER NOT NULL DEFAULT 1,
    allow_download INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS images (
    id            INTEGER PRIMARY KEY,
    album_id      INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    stored_name   TEXT NOT NULL UNIQUE,
    width         INTEGER NOT NULL DEFAULT 0,
    height        INTEGER NOT NULL DEFAULT 0,
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    exif_json     TEXT NOT NULL DEFAULT '{}',
    taken_at      TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_images_album ON images(album_id);
CREATE TABLE IF NOT EXISTS album_access (
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (album_id, user_id)
);
CREATE TABLE IF NOT EXISTS share_links (
    id             INTEGER PRIMARY KEY,
    token          TEXT NOT NULL UNIQUE,
    album_id       INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    label          TEXT NOT NULL DEFAULT '',
    password_hash  TEXT,
    expires_at     TEXT,
    allow_download INTEGER NOT NULL DEFAULT 1,
    views          INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    last_used      TEXT
);
CREATE TABLE IF NOT EXISTS favorites (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, image_id)
)
"""

# Version 2: Moodcase-Studio-Funktionen (Gäste, Auswahl, Kommentare, Workflow, Branding, Protokoll)
V2 = """
CREATE TABLE users_new (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'model' CHECK (role IN ('admin','model','guest')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_login    TEXT,
    share_id      INTEGER REFERENCES share_links(id) ON DELETE CASCADE
);
INSERT INTO users_new (id, username, display_name, email, password_hash, role, active, created_at, last_login)
    SELECT id, username, display_name, email, password_hash, role, active, created_at, last_login FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;

ALTER TABLE albums ADD COLUMN status TEXT NOT NULL DEFAULT 'auswahl';
ALTER TABLE albums ADD COLUMN expires_at TEXT;
ALTER TABLE albums ADD COLUMN watermark INTEGER NOT NULL DEFAULT 0;
ALTER TABLE albums ADD COLUMN selection_enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE albums ADD COLUMN selection_limit INTEGER NOT NULL DEFAULT 0;
ALTER TABLE albums ADD COLUMN comments_enabled INTEGER NOT NULL DEFAULT 1;

ALTER TABLE images ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE images ADD COLUMN updated_at TEXT;

ALTER TABLE share_links ADD COLUMN can_select INTEGER NOT NULL DEFAULT 1;
ALTER TABLE share_links ADD COLUMN can_comment INTEGER NOT NULL DEFAULT 1;
ALTER TABLE share_links ADD COLUMN watermark INTEGER NOT NULL DEFAULT 0;

CREATE TABLE selection_done (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    album_id     INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    completed_at TEXT NOT NULL DEFAULT (datetime('now')),
    note         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, album_id)
);

CREATE TABLE comments (
    id          INTEGER PRIMARY KEY,
    image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author_name TEXT NOT NULL DEFAULT '',
    parent_id   INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'comment',
    body        TEXT NOT NULL,
    x REAL, y REAL, w REAL, h REAL,
    resolved_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_comments_image ON comments(image_id);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE events (
    id         INTEGER PRIMARY KEY,
    at         TEXT NOT NULL DEFAULT (datetime('now')),
    action     TEXT NOT NULL,
    actor_id   INTEGER,
    actor_name TEXT NOT NULL DEFAULT '',
    album_id   INTEGER,
    image_id   INTEGER,
    detail     TEXT NOT NULL DEFAULT '',
    ip         TEXT NOT NULL DEFAULT '',
    notify     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_events_album ON events(album_id);
CREATE INDEX idx_events_notify ON events(notify, id)
"""

# Version 3: Projekte (organisatorisches Gefäss) mit Ordnerbaum; Alben sind die Blätter
V3 = """
CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE folders (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_folders_project ON folders(project_id, parent_id);

ALTER TABLE albums ADD COLUMN project_id INTEGER REFERENCES projects(id);
ALTER TABLE albums ADD COLUMN folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL;
CREATE INDEX idx_albums_project ON albums(project_id, folder_id);

INSERT INTO projects (slug, title) SELECT 'allgemein', 'Allgemein' WHERE EXISTS (SELECT 1 FROM albums);
UPDATE albums SET project_id = (SELECT id FROM projects WHERE slug = 'allgemein') WHERE project_id IS NULL
"""

# Version 4: Permalinks auch für ganze Projekte oder Ordner; Kundenzugriff pro Projekt
V4 = """
CREATE TABLE share_links_new (
    id             INTEGER PRIMARY KEY,
    token          TEXT NOT NULL UNIQUE,
    album_id       INTEGER REFERENCES albums(id) ON DELETE CASCADE,
    project_id     INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    folder_id      INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    label          TEXT NOT NULL DEFAULT '',
    password_hash  TEXT,
    expires_at     TEXT,
    allow_download INTEGER NOT NULL DEFAULT 1,
    views          INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    last_used      TEXT,
    can_select     INTEGER NOT NULL DEFAULT 1,
    can_comment    INTEGER NOT NULL DEFAULT 1,
    watermark      INTEGER NOT NULL DEFAULT 0,
    CHECK ((album_id IS NULL) != (project_id IS NULL))
);
INSERT INTO share_links_new (id, token, album_id, label, password_hash, expires_at, allow_download, views,
                             created_at, last_used, can_select, can_comment, watermark)
    SELECT id, token, album_id, label, password_hash, expires_at, allow_download, views,
           created_at, last_used, can_select, can_comment, watermark FROM share_links;
DROP TABLE share_links;
ALTER TABLE share_links_new RENAME TO share_links;
CREATE INDEX idx_share_album ON share_links(album_id);
CREATE INDEX idx_share_project ON share_links(project_id);

CREATE TABLE project_access (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, user_id)
)
"""

# Version 5: Rolle «plugin» (z. B. Lightroom) mit API-Schlüssel; RAW-Dateien als Begleitdatei zum Bild
V5 = """
CREATE TABLE users_new (
    id             INTEGER PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name   TEXT NOT NULL DEFAULT '',
    email          TEXT NOT NULL DEFAULT '',
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'model' CHECK (role IN ('admin','model','guest','plugin')),
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    last_login     TEXT,
    share_id       INTEGER REFERENCES share_links(id) ON DELETE CASCADE,
    api_token_hash TEXT
);
INSERT INTO users_new (id, username, display_name, email, password_hash, role, active, created_at, last_login, share_id)
    SELECT id, username, display_name, email, password_hash, role, active, created_at, last_login, share_id FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;
CREATE UNIQUE INDEX idx_users_token ON users(api_token_hash) WHERE api_token_hash IS NOT NULL;

ALTER TABLE images ADD COLUMN raw_name TEXT;
ALTER TABLE images ADD COLUMN raw_stored TEXT;
ALTER TABLE images ADD COLUMN raw_size INTEGER NOT NULL DEFAULT 0;
ALTER TABLE albums ADD COLUMN allow_raw INTEGER NOT NULL DEFAULT 0
"""

MIGRATIONS = [V1, V2, V3, V4, V5]

# Bilder werden nach Aufnahmezeit, dann Dateiname sortiert.
IMAGE_ORDER = "ORDER BY COALESCE(taken_at, created_at), original_name, id"


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # WAL ist dauerhaft in der Datei gesetzt (siehe init)
    return conn


@contextmanager
def get():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _statements(sql):
    return [s.strip() for s in sql.split(";") if s.strip()]


def init():
    """Führt ausstehende Migrationen aus. Sicher bei mehreren gleichzeitig startenden Workern."""
    conn = sqlite3.connect(config.DB_PATH, timeout=60, isolation_level=None)
    try:
        # Der Wechsel in den WAL-Modus wartet nicht auf andere Worker – daher selbst wiederholen
        for attempt in range(100):
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                break
            except sqlite3.OperationalError:
                time.sleep(0.1)
        conn.execute("PRAGMA foreign_keys = OFF")  # nötig für den Tabellenumbau
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
            version = 1  # Installation von vor dem Migrationssystem
        for n, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            for stmt in _statements(sql):
                conn.execute(stmt)
            conn.execute("PRAGMA user_version = {}".format(n))
        conn.execute("PRAGMA user_version = {}".format(max(version, len(MIGRATIONS))))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
