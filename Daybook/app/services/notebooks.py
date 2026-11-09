"""Notebooks (folders for notes)."""
from __future__ import annotations

from .. import db
from ..bus import changed
from ..util import new_id, now_ms

DEFAULTS = [("Work", "#2B5F97"), ("Personal", "#1F7A72"), ("Journal", "#6A4C9C")]


def seed() -> None:
    if db.scalar("SELECT count(*) FROM notebooks"):
        return
    with db.tx() as c:
        for i, (name, color) in enumerate(DEFAULTS):
            c.execute("INSERT INTO notebooks(id, name, color, sort, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                      (new_id(), name, color, i, now_ms(), now_ms()))


def all_with_counts() -> list[dict]:
    return db.q(
        """SELECT b.*, (SELECT count(*) FROM notes n WHERE n.notebook_id = b.id AND n.trashed_at IS NULL AND n.archived = 0) AS count
           FROM notebooks b ORDER BY b.lib_dir IS NULL, b.sort, lower(b.name)"""
    )


def create(name: str, color: str | None = None) -> dict:
    name = (name or "").strip()[:60]
    if not name:
        raise ValueError("Give the notebook a name.")
    if db.q1("SELECT id FROM notebooks WHERE lower(name) = lower(?)", (name,)):
        raise ValueError("You already have a notebook with that name.")
    nid = new_id()
    sort = (db.scalar("SELECT max(sort) FROM notebooks") or 0) + 1
    with db.tx() as c:
        c.execute("INSERT INTO notebooks(id, name, color, sort, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                  (nid, name, color or "#56665C", sort, now_ms(), now_ms()))
    changed("notebooks", nid)
    return db.q1("SELECT * FROM notebooks WHERE id = ?", (nid,))


def update(nid: str, data: dict) -> dict:
    row = db.q1("SELECT * FROM notebooks WHERE id = ?", (nid,))
    if not row:
        from .notes import NotFound

        raise NotFound(nid)
    if row.get("lib_dir") is not None and data.get("name") and data["name"].strip() != row["name"]:
        raise ValueError("This notebook is a folder in your library. Rename the folder in Finder instead.")
    name = (data.get("name") or row["name"]).strip()[:60]
    color = data.get("color") or row["color"]
    sort = int(data.get("sort", row["sort"]))
    with db.tx() as c:
        c.execute("UPDATE notebooks SET name = ?, color = ?, sort = ?, updated_at = ? WHERE id = ?",
                  (name, color, sort, now_ms(), nid))
    changed("notebooks", nid)
    return db.q1("SELECT * FROM notebooks WHERE id = ?", (nid,))


def delete(nid: str) -> None:
    row = db.q1("SELECT lib_dir FROM notebooks WHERE id = ?", (nid,))
    if row and row["lib_dir"] is not None:
        raise ValueError("This notebook is a folder in your library. Remove the folder in Finder instead.")
    with db.tx() as c:
        c.execute("UPDATE notes SET notebook_id = NULL WHERE notebook_id = ?", (nid,))
        c.execute("DELETE FROM notebooks WHERE id = ?", (nid,))
    changed("notebooks", nid)
