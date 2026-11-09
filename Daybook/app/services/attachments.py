"""Attachment files (imported originals, pasted images, dropped files)."""
from __future__ import annotations

import hashlib
import mimetypes
from datetime import datetime
from pathlib import Path

from .. import db
from ..util import new_id, now_ms, safe_filename

INLINE_SAFE = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain",
    "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/wav", "audio/x-wav", "audio/webm", "audio/ogg",
    "video/mp4", "video/webm",
}


def guess_mime(filename: str, fallback: str = "application/octet-stream") -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or fallback


def save(data: bytes, filename: str, *, note_id: str | None = None, mime: str | None = None, role: str = "inline") -> dict:
    p = db.get_paths()
    aid = new_id(16)
    name = safe_filename(filename, "file")
    sub = Path(datetime.now().strftime("%Y/%m"))
    folder = p.attachments / sub
    folder.mkdir(parents=True, exist_ok=True)
    rel = sub / f"{aid}-{name}"
    (p.attachments / rel).write_bytes(data)
    row = {
        "id": aid,
        "note_id": note_id,
        "filename": name,
        "mime": mime or guess_mime(name),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "rel_path": str(rel),
        "role": role,
        "created_at": now_ms(),
    }
    with db.tx() as c:
        c.execute(
            "INSERT INTO attachments(id, note_id, filename, mime, size, sha256, rel_path, role, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            tuple(row.values()),
        )
    return row


def get(aid: str) -> dict | None:
    return db.q1("SELECT * FROM attachments WHERE id = ?", (aid,))


def path_of(row: dict) -> Path:
    base = db.get_paths().attachments.resolve()
    target = (base / row["rel_path"]).resolve()
    if base not in target.parents:
        raise PermissionError("Invalid attachment path")
    return target


def attach_to(aid: str, note_id: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE attachments SET note_id = ? WHERE id = ?", (note_id, aid))


def delete(aid: str) -> None:
    row = get(aid)
    if not row:
        return
    try:
        path_of(row).unlink(missing_ok=True)
    except (OSError, PermissionError):
        pass
    with db.tx() as c:
        c.execute("DELETE FROM attachments WHERE id = ?", (aid,))


def url(aid: str) -> str:
    return f"/api/attachments/{aid}"
