"""Notes, notebooks, tags, daily notes, templates, attachments."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from .. import db
from ..config import MAX_UPLOAD_MB
from ..services import attachments, backup, notebooks, notes

router = APIRouter(prefix="/api")


# Notes ----------------------------------------------------------------------
@router.get("/notes")
def list_notes(view: str = "all", notebook: str | None = None, tag: str | None = None, q: str | None = None,
               sort: str = "updated", date: str | None = None, kind: str | None = None, limit: int = 200, offset: int = 0):
    res = notes.list_notes(view=view, notebook_id=notebook, tag=tag, query=q, sort=sort, linked_date=date, lib_kind=kind,
                           limit=min(max(limit, 1), 500), offset=max(offset, 0))
    res["counts"] = notes.counts()
    if q:
        res["filters"] = notes.parse_search(q)[3]
    return res


@router.post("/notes")
def create_note(payload: dict = Body(default_factory=dict)):
    title, body = payload.get("title", ""), payload.get("body", "")
    if payload.get("template_id"):
        t_title, t_body = notes.render_template(payload["template_id"])
        title = title or t_title
        body = body or t_body
    notebook_id = payload.get("notebook_id")
    if not notebook_id and "notebook_id" not in payload:
        notebook_id = default_notebook()
    return notes.create(
        title=title, body=body, notebook_id=notebook_id, tags=payload.get("tags"),
        linked_date=payload.get("linked_date"), color=payload.get("color"), pinned=bool(payload.get("pinned")),
        kind=payload.get("kind") if payload.get("kind") in ("note", "ai") else "note",
    )


def default_notebook() -> str | None:
    """New notes go to the library's notes folder when that's the setting (and a library is connected)."""
    from ..services import library

    s = db.get_settings()["library"]
    if s.get("new_notes", "library") != "library":
        return None
    root = library.engine.root()
    if root is None:
        return None
    folder = (s.get("new_notes_dir") or "Notes").strip().strip("/")
    (root / folder).mkdir(exist_ok=True)
    return library.notebook_for(folder, root.name)


@router.get("/notes/{note_id}")
def get_note(note_id: str):
    return notes.get(note_id)


@router.post("/notes/{note_id}/date")
def set_note_date(note_id: str, payload: dict = Body(...)):
    """Pick the day a note belongs to ({date, end?, time?}), mark it undated ({none: true}),
    or hand it back to automatic dating ({clear: true})."""
    from ..services import library

    notes.get_row(note_id)
    if payload.get("clear"):
        library.engine.set_date(note_id, clear=True)
    elif payload.get("none"):
        library.engine.set_date(note_id, none=True, by="you")
    else:
        if not payload.get("date"):
            raise ValueError("Pick a date.")
        library.engine.set_date(note_id, day=payload["date"], end=payload.get("end") or None,
                                time_=payload.get("time") or None, by="you")
    return notes.get(note_id)


@router.put("/notes/{note_id}")
def update_note(note_id: str, payload: dict = Body(...)):
    base = payload.pop("base_rev", None)
    force = bool(payload.pop("force", False))
    return notes.update(note_id, payload, base_rev=base, force=force)


@router.delete("/notes/{note_id}")
def delete_note(note_id: str, permanent: bool = False):
    if permanent:
        notes.delete_forever(note_id)
    else:
        notes.trash(note_id)
    return {"ok": True}


@router.post("/notes/{note_id}/restore")
def restore_note(note_id: str):
    return notes.restore(note_id)


@router.post("/notes/{note_id}/duplicate")
def duplicate_note(note_id: str):
    return notes.duplicate(note_id)


@router.post("/trash/empty")
def empty_trash():
    return {"deleted": notes.empty_trash()}


@router.get("/notes/{note_id}/revisions")
def list_revisions(note_id: str):
    return notes.revisions(note_id)


@router.get("/notes/{note_id}/revisions/{rev_id}")
def get_revision(note_id: str, rev_id: int):
    return notes.revision(note_id, rev_id)


@router.post("/notes/{note_id}/revisions/{rev_id}/restore")
def restore_revision(note_id: str, rev_id: int):
    return notes.restore_revision(note_id, rev_id)


@router.put("/notes/{note_id}/suggestions")
def set_suggestions(note_id: str, payload: dict = Body(default_factory=dict)):
    import json

    notes.get_row(note_id)
    sug = payload.get("suggestions")
    with db.tx() as c:
        c.execute("UPDATE notes SET ai_suggestions = ? WHERE id = ?", (json.dumps(sug) if sug else None, note_id))
    return notes.get(note_id)


@router.get("/notes/{note_id}/export.md")
def export_note(note_id: str):
    name, md = backup.export_note_markdown(note_id)
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


# Locked notes -------------------------------------------------------------------
@router.post("/notes/{note_id}/lock")
def lock_note(note_id: str, payload: dict = Body(...)):
    return notes.lock(note_id, payload.get("passphrase") or "")


@router.post("/notes/{note_id}/unlock")
def unlock_note(note_id: str, payload: dict = Body(...)):
    return notes.unlock_view(note_id, payload.get("passphrase") or "")


@router.post("/notes/{note_id}/save-locked")
def save_locked(note_id: str, payload: dict = Body(...)):
    return notes.save_locked(note_id, payload.get("passphrase") or "", payload.get("title"), payload.get("body") or "",
                             payload.get("base_rev"))


@router.post("/notes/{note_id}/remove-lock")
def remove_lock(note_id: str, payload: dict = Body(...)):
    return notes.remove_lock(note_id, payload.get("passphrase") or "")


@router.post("/vault/change")
def change_vault(payload: dict = Body(...)):
    return {"reencrypted": notes.change_vault(payload.get("old") or "", payload.get("new") or "")}


@router.get("/vault")
def vault_status():
    from .. import security

    return {"set": security.vault_is_set()}


# Tags, notebooks, daily, templates --------------------------------------------------
@router.get("/tags")
def tags():
    return notes.all_tags()


@router.get("/notebooks")
def list_notebooks():
    return notebooks.all_with_counts()


@router.post("/notebooks")
def create_notebook(payload: dict = Body(...)):
    return notebooks.create(payload.get("name") or "", payload.get("color"))


@router.put("/notebooks/{nid}")
def update_notebook(nid: str, payload: dict = Body(...)):
    return notebooks.update(nid, payload)


@router.delete("/notebooks/{nid}")
def delete_notebook(nid: str):
    notebooks.delete(nid)
    return {"ok": True}


@router.get("/daily/{day}")
def daily_note(day: str, create: bool = True):
    note = notes.daily(day, create_if_missing=create)
    if note is None:
        raise HTTPException(404, "No daily note for that day.")
    return note


@router.get("/templates")
def templates():
    return [{k: t[k] for k in ("id", "name", "builtin")} for t in notes.templates()]


# Attachments -------------------------------------------------------------------------
@router.post("/attachments")
async def upload_attachment(file: UploadFile = File(...), note_id: str | None = Form(None)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Files over {MAX_UPLOAD_MB} MB can't be attached.")
    if note_id and not db.q1("SELECT id FROM notes WHERE id = ?", (note_id,)):
        note_id = None
    mime = file.content_type or attachments.guess_mime(file.filename or "file")
    row = attachments.save(data, file.filename or "file", note_id=note_id, mime=mime,
                           role="inline" if mime.startswith("image/") else "file")
    return {"id": row["id"], "url": attachments.url(row["id"]), "filename": row["filename"], "mime": row["mime"], "size": row["size"]}


@router.get("/attachments/{aid}")
def get_attachment(aid: str, download: bool = False):
    row = attachments.get(aid)
    if not row:
        raise HTTPException(404, "That file no longer exists.")
    path = attachments.path_of(row)
    if not path.exists():
        raise HTTPException(404, "That file is missing from disk.")
    mime = row["mime"] or "application/octet-stream"
    inline = not download and mime in attachments.INLINE_SAFE
    headers = {"Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox"}
    disp = "inline" if inline else "attachment"
    headers["Content-Disposition"] = f"{disp}; filename*=UTF-8''{quote(row['filename'])}"
    return FileResponse(path, media_type=mime if inline else "application/octet-stream", headers=headers)


@router.delete("/attachments/{aid}")
def delete_attachment(aid: str):
    attachments.delete(aid)
    return {"ok": True}
