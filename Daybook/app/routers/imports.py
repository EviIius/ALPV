"""Document import: uploads, job status, Inbox folder."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from .. import db, security
from ..config import MAX_UPLOAD_MB
from ..services import converters, importer
from ..services.ocr import engine_status
from ..services.transcribe import available_engines

router = APIRouter(prefix="/api/import")


@router.get("/info")
def info():
    settings = db.get_settings()
    return {
        "formats": converters.supported_extensions(),
        "ocr": engine_status(settings),
        "transcription": {"engines": available_engines(), "selected": settings["import"].get("transcribe", "auto")},
        "legacy_word": {"textutil": sys.platform == "darwin", "libreoffice": bool(__import__("shutil").which("soffice"))},
        "inbox": importer.watcher.status(),
        "max_mb": MAX_UPLOAD_MB,
    }


def _add_to_library(name: str, data: bytes, folder: str) -> dict:
    """Save an uploaded file into a library folder, index it right away, and log it like an import."""
    import json

    from ..services import library
    from ..util import new_id, now_ms, safe_filename

    root = library.engine.root()
    if root is None:
        raise HTTPException(409, "No library folder is connected.")
    target_dir = library.safe_rel(root, folder) if folder else root
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = library._unique(target_dir / safe_filename(name, "file"))
    library._atomic_write(dest, data)
    rel = dest.relative_to(root).as_posix()
    ctx = library.engine._context(root)
    note_id = library.engine.index_file(root, rel, ctx, new_file=True)
    job_id, ts = new_id(), now_ms()
    with db.tx() as c:
        c.execute("INSERT INTO imports(id, filename, origin, status, message, note_id, options, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (job_id, name, "library", "done", f"Saved to {root.name}/{rel}", note_id, json.dumps({"library_path": rel}), ts, ts))
    from ..bus import changed

    changed("notes", note_id)
    return importer.job_api(db.q1("SELECT * FROM imports WHERE id = ?", (job_id,)))


@router.post("")
async def upload(files: list[UploadFile] = File(...), notebook_id: str | None = Form(None),
                 ai_mode: str | None = Form(None), keep_original: bool = Form(True), library_dir: str | None = Form(None)):
    from anyio import to_thread

    jobs = []
    for f in files[:50]:
        data = await f.read()
        name = Path(f.filename or "file").name
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"{name} is over {MAX_UPLOAD_MB} MB.")
        if not converters.is_supported(name):
            raise HTTPException(415, f"{name}: {Path(name).suffix or 'this file type'} isn't supported yet.")
        from ..services import library as _lib

        if library_dir is not None and library_dir != "" and _lib.classify(name):
            jobs.append(await to_thread.run_sync(_add_to_library, name, data, "" if library_dir == "/" else library_dir))
            continue
        jobs.append(importer.enqueue(name, data, origin="upload", options={
            "notebook_id": notebook_id or None,
            "ai_mode": ai_mode if ai_mode in ("off", "suggest", "auto") else None,
            "keep_original": keep_original,
        }))
    return jobs


@router.get("/jobs")
def jobs(limit: int = 50):
    return importer.recent_jobs(min(limit, 200))


@router.delete("/jobs/{job_id}")
def remove_job(job_id: str):
    with db.tx() as c:
        c.execute("DELETE FROM imports WHERE id = ? AND status IN ('done', 'error')", (job_id,))
    return {"ok": True}


@router.post("/jobs/clear")
def clear_jobs():
    with db.tx() as c:
        c.execute("DELETE FROM imports WHERE status IN ('done', 'error')")
    return {"ok": True}


@router.post("/inbox/reveal")
def reveal_inbox(request: Request):
    """Open the Inbox folder in Finder — only when you're sitting at the Mac."""
    if security.classify_ip(request.state.client_ip) != "loopback" or sys.platform != "darwin":
        raise HTTPException(400, "Open the folder on the Mac running Daybook.")
    folder = Path(db.get_settings()["import"].get("inbox_dir") or "~/Notes Inbox").expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(["open", str(folder)], timeout=10)
    return {"ok": True}
