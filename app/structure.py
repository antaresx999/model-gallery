"""Projekte und Ordnerbäume. Ein Projekt ist das organisatorische Gefäss, Ordner gliedern es beliebig tief,
Alben sind immer die Blätter und enthalten die Bilder."""
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException

# Alben mit Titelbild und Bildanzahl; dazu Projekt und Ordner
ALBUM_LIST_SQL = """
    SELECT a.*,
           (SELECT COUNT(*) FROM images i WHERE i.album_id = a.id) AS image_count,
           COALESCE(a.cover_image_id,
                    (SELECT i.id FROM images i WHERE i.album_id = a.id
                     ORDER BY COALESCE(i.taken_at, i.created_at), i.original_name LIMIT 1)) AS cover_id,
           (SELECT version FROM images i WHERE i.id = COALESCE(a.cover_image_id,
                    (SELECT i2.id FROM images i2 WHERE i2.album_id = a.id
                     ORDER BY COALESCE(i2.taken_at, i2.created_at), i2.original_name LIMIT 1))) AS cover_version
    FROM albums a
"""
ALBUM_ORDER = " ORDER BY a.shoot_date DESC, a.created_at DESC"


def visible_albums(conn, user, where="", args=()):
    """Alle Alben, die der Benutzer in der Galerie sieht (Admin: alle)."""
    if user["role"] == "admin":
        sql = ALBUM_LIST_SQL + (" WHERE " + where if where else "") + ALBUM_ORDER
        return conn.execute(sql, list(args)).fetchall()
    sql = ALBUM_LIST_SQL + """ WHERE a.published = 1
        AND (a.expires_at IS NULL OR a.expires_at = '' OR a.expires_at >= date('now','localtime'))
        AND (EXISTS(SELECT 1 FROM album_access aa WHERE aa.album_id = a.id AND aa.user_id = ?)
             OR EXISTS(SELECT 1 FROM project_access pa WHERE pa.project_id = a.project_id AND pa.user_id = ?))"""
    sql += (" AND " + where if where else "") + ALBUM_ORDER
    return conn.execute(sql, [user["id"], user["id"]] + list(args)).fetchall()


def project_by_slug(conn, slug):
    p = conn.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
    if not p:
        raise HTTPException(404, "Projekt nicht gefunden.")
    return p


def project_by_id(conn, project_id):
    p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Projekt nicht gefunden.")
    return p


def folders_of(conn, project_id) -> Dict[int, dict]:
    rows = conn.execute("SELECT * FROM folders WHERE project_id=? ORDER BY name COLLATE NOCASE, id",
                        (project_id,)).fetchall()
    return {r["id"]: dict(r) for r in rows}


def folder_chain(folders: Dict[int, dict], folder_id: Optional[int]) -> List[dict]:
    """Ordner von der Projektebene bis zum angegebenen Ordner."""
    chain, seen = [], set()
    while folder_id and folder_id in folders and folder_id not in seen:
        seen.add(folder_id)
        chain.append(folders[folder_id])
        folder_id = folders[folder_id]["parent_id"]
    return list(reversed(chain))


def children(folders: Dict[int, dict], parent_id: Optional[int]) -> List[dict]:
    return [f for f in folders.values() if f["parent_id"] == parent_id]


def descendants(folders: Dict[int, dict], folder_id: int) -> set:
    """Ordner-ID und alle darunterliegenden Ordner."""
    out, todo = set(), [folder_id]
    while todo:
        fid = todo.pop()
        if fid in out:
            continue
        out.add(fid)
        todo.extend(f["id"] for f in folders.values() if f["parent_id"] == fid)
    return out


def tree(folders: Dict[int, dict], parent_id: Optional[int] = None, depth: int = 0) -> List[Tuple[int, dict]]:
    """Ordner in Baumreihenfolge als (Tiefe, Ordner)."""
    out = []
    for f in children(folders, parent_id):
        out.append((depth, f))
        out.extend(tree(folders, f["id"], depth + 1))
    return out


def ancestors_with_albums(folders: Dict[int, dict], albums) -> set:
    """Ordner, die (direkt oder in Unterordnern) mindestens eines der Alben enthalten."""
    out = set()
    for a in albums:
        out.update(f["id"] for f in folder_chain(folders, a["folder_id"]))
    return out


def location_options(conn) -> List[Tuple[str, str]]:
    """Auswahl für den Ablageort eines Albums: Projekt-Ebene oder Ordner."""
    opts = []
    for p in conn.execute("SELECT * FROM projects ORDER BY title COLLATE NOCASE").fetchall():
        opts.append(("p:{}".format(p["id"]), p["title"]))
        folders = folders_of(conn, p["id"])
        for depth, f in tree(folders):
            path = " / ".join(x["name"] for x in folder_chain(folders, f["id"]))
            opts.append(("f:{}".format(f["id"]), "{} / {}".format(p["title"], path)))
    return opts


def location_value(album=None, project_id=None, folder_id=None) -> str:
    if album is not None:
        project_id, folder_id = album["project_id"], album["folder_id"]
    if folder_id:
        return "f:{}".format(folder_id)
    return "p:{}".format(project_id) if project_id else ""


def parse_location(conn, value: str) -> Tuple[Optional[int], Optional[int]]:
    """'p:3' → (3, None), 'f:7' → (Projekt des Ordners, 7). Ungültig → (None, None)."""
    kind, _, raw = (value or "").partition(":")
    if not raw.isdigit():
        return None, None
    if kind == "p":
        row = conn.execute("SELECT id FROM projects WHERE id=?", (int(raw),)).fetchone()
        return (row["id"], None) if row else (None, None)
    if kind == "f":
        row = conn.execute("SELECT id, project_id FROM folders WHERE id=?", (int(raw),)).fetchone()
        return (row["project_id"], row["id"]) if row else (None, None)
    return None, None


def project_cards(conn, user):
    """Projekte mit sichtbaren Alben (Admin: alle Projekte) inkl. Titelbild und Anzahl Alben."""
    albums = visible_albums(conn, user)
    by_project = {}
    for a in albums:
        by_project.setdefault(a["project_id"], []).append(a)
    projects = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC, title COLLATE NOCASE").fetchall()
    cards = []
    for p in projects:
        items = by_project.get(p["id"], [])
        if not items and user["role"] != "admin":
            continue
        cover = next((a for a in items if a["cover_id"]), None)
        cards.append({"project": p, "album_count": len(items),
                      "image_count": sum(a["image_count"] for a in items),
                      "cover_id": cover["cover_id"] if cover else None,
                      "cover_version": cover["cover_version"] if cover else None})
    return cards, by_project.get(None, [])


def share_scope(conn, share):
    """Projekt und (optional) Ordner eines Projekt-Permalinks samt Ordnerliste und erlaubten Ordner-IDs."""
    project = conn.execute("SELECT * FROM projects WHERE id=?", (share["project_id"],)).fetchone()
    folders = folders_of(conn, share["project_id"]) if project else {}
    allowed = descendants(folders, share["folder_id"]) if share["folder_id"] else None
    return project, folders, allowed


def share_covers(conn, share, album) -> bool:
    """Gilt der Permalink für dieses Album?"""
    if share["album_id"]:
        return share["album_id"] == album["id"]
    if not share["project_id"] or album["project_id"] != share["project_id"]:
        return False
    if not share["folder_id"]:
        return True
    return album["folder_id"] in descendants(folders_of(conn, share["project_id"]), share["folder_id"])


def share_albums(conn, share):
    """Veröffentlichte, nicht abgelaufene Alben eines Projekt-Permalinks."""
    rows = conn.execute(ALBUM_LIST_SQL + """ WHERE a.project_id = ? AND a.published = 1
        AND (a.expires_at IS NULL OR a.expires_at = '' OR a.expires_at >= date('now','localtime'))""" + ALBUM_ORDER,
                        (share["project_id"],)).fetchall()
    if share["folder_id"]:
        allowed = descendants(folders_of(conn, share["project_id"]), share["folder_id"])
        rows = [a for a in rows if a["folder_id"] in allowed]
    return rows
