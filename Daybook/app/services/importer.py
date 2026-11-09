"""Import pipeline: uploaded files and the watched Inbox folder become notes.

    file ─► convert to Markdown (on this machine) ─► note (+ original attached)
         └► optional local-AI pass: title, summary, tags, action items, dates
"""
from __future__ import annotations

import json
import shutil
import time
import zipfile
from pathlib import Path

from .. import db
from ..bus import bus
from ..util import jloads, new_id, now_ms, safe_filename, title_from_filename, today_str
from . import attachments, converters, events as events_svc, notes as notes_svc, tasks as tasks_svc
from .ocr import make_ocr
from .transcribe import make_transcriber

IGNORED_PREFIXES = ("~$", ".~lock", "._")
IGNORED_SUFFIXES = (".tmp", ".crdownload", ".part", ".download", ".icloud", ".ds_store", ".lnk")
_queue_hook = {"put": None}


def set_queue(put) -> None:
    _queue_hook["put"] = put


def _publish(job: dict) -> None:
    bus.publish({"type": "import", "job": job})


def job_api(r: dict) -> dict:
    out = {k: r[k] for k in ("id", "filename", "origin", "status", "message", "note_id", "created_at", "updated_at")}
    opts = jloads(r.get("options"), {})
    out["suggestions"] = bool(opts.get("has_suggestions"))
    return out


def _set(job_id: str, **fields) -> dict:
    fields["updated_at"] = now_ms()
    with db.tx() as c:
        c.execute(f"UPDATE imports SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), job_id))
    row = db.q1("SELECT * FROM imports WHERE id = ?", (job_id,))
    job = job_api(row)
    _publish(job)
    return job


def enqueue(filename: str, src: Path | bytes, *, origin: str = "upload", options: dict | None = None,
            inbox_path: str | None = None) -> dict:
    p = db.get_paths()
    job_id = new_id()
    tmp = p.tmp / f"{job_id}-{safe_filename(filename)}"
    if isinstance(src, (bytes, bytearray)):
        tmp.write_bytes(src)
    else:
        shutil.copyfile(src, tmp)
    opts = dict(options or {})
    opts["tmp"] = str(tmp)
    if inbox_path:
        opts["inbox_path"] = inbox_path
    ts = now_ms()
    with db.tx() as c:
        c.execute("INSERT INTO imports(id, filename, origin, status, message, options, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                  (job_id, filename, origin, "queued", None, json.dumps(opts), ts, ts))
    job = job_api(db.q1("SELECT * FROM imports WHERE id = ?", (job_id,)))
    _publish(job)
    if _queue_hook["put"]:
        _queue_hook["put"](job_id)
    return job


def recent_jobs(limit: int = 50) -> list[dict]:
    return [job_api(r) for r in db.q("SELECT * FROM imports ORDER BY created_at DESC LIMIT ?", (limit,))]


def pending_job_ids() -> list[str]:
    return [r["id"] for r in db.q("SELECT id FROM imports WHERE status IN ('queued', 'converting', 'ai') ORDER BY created_at")]


# ---------------------------------------------------------------------------
# Processing (runs in a worker thread)
# ---------------------------------------------------------------------------
def _replace_assets(md: str, assets: list[converters.Asset]) -> tuple[str, list[str]]:
    ids = []
    for i, a in enumerate(assets):
        row = attachments.save(a.data, a.name, mime=a.mime, role="inline" if a.mime.startswith("image/") else "file")
        ids.append(row["id"])
        md = md.replace(f"asset://{i})", f"{attachments.url(row['id'])})")
    return md, ids


def _ai_models(settings: dict):
    from .ai import AIClient, pick_models, run_sync

    client = AIClient.from_settings(settings)
    try:
        available = run_sync(client.models())
    except Exception:
        available = []
    return client, pick_models(settings, available)


def _note_from_file(path: Path, filename: str, settings: dict, notebook_id: str | None, keep_original: bool) -> tuple[dict, list[str]]:
    conv = converters.convert(path, filename, ocr=make_ocr(settings), transcribe=make_transcriber(settings),
                              max_rows=int(settings["import"].get("max_table_rows", 300)))
    md, asset_ids = _replace_assets(conv.markdown, conv.assets)
    ext = path.suffix.lower()
    original = None
    if keep_original or ext in converters.AUDIO:
        original = attachments.save(path.read_bytes(), filename, role="original")
        if ext in converters.AUDIO:
            md = f"[Recording: {filename}]({attachments.url(original['id'])})\n\n" + md
    note = notes_svc.create(title=conv.title or title_from_filename(filename), body=md, kind="import",
                            source_name=filename, notebook_id=notebook_id)
    for aid in asset_ids:
        attachments.attach_to(aid, note["id"])
    if original:
        attachments.attach_to(original["id"], note["id"])
    return note, conv.warnings


def _apply_ai(note: dict, filename: str, settings: dict, mode: str) -> tuple[str, bool]:
    """Returns (message, stored_suggestions)."""
    from .ai import run_sync
    from .ai_tasks import structure_import

    client, models = _ai_models(settings)
    if not models["smart"]:
        return "Local AI isn't set up, so the note was filed as-is.", False
    existing = [t["tag"] for t in notes_svc.all_tags()]
    body = notes_svc.get_row(note["id"])["body"]
    result = run_sync(structure_import(client, models, filename, body, today_str(), existing))
    if mode == "auto":
        patch = {"summary": result["summary"] or None, "tags": list(dict.fromkeys(note["tags"] + result["tags"]))}
        if result["title"]:
            patch["title"] = result["title"]
        notes_svc.update(note["id"], patch, force=True)
        for item in result["action_items"]:
            tasks_svc.create({"title": item["title"], "due_date": item["due"], "priority": item["priority"],
                              "note_id": note["id"], "list": "From imports"})
        if result["events"]:
            _store_suggestions(note["id"], {"events": result["events"], "model": result["model"], "ms": result["ms"]})
            return f"Filed by {result['model']}. {len(result['events'])} date(s) to review.", True
        return f"Filed by {result['model']} in {result['ms'] / 1000:.1f}s.", False
    _store_suggestions(note["id"], {**result})
    return f"Suggestions from {result['model']} are ready to review.", True


def _store_suggestions(note_id: str, sug: dict) -> None:
    with db.tx() as c:
        c.execute("UPDATE notes SET ai_suggestions = ? WHERE id = ?", (json.dumps(sug), note_id))


def _process_zip(path: Path, settings: dict, notebook_id: str | None, keep_original: bool) -> tuple[list[str], list[str]]:
    from . import backup

    created, warnings = [], []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if not n.endswith("/") and "__MACOSX" not in n and not Path(n).name.startswith(".")]
        if "daybook.json" in names:
            counts = backup.import_zip(path)
            return [], [f"Restored a Daybook export: {counts}"]
        total = sum(z.getinfo(n).file_size for n in names)
        if total > 1_000_000_000 or len(names) > 1000:
            raise converters.ConversionError("This archive is too large to import in one go (1 GB / 1000 files max).")
        folder_books: dict[str, str | None] = {}
        tmpdir = db.get_paths().tmp / f"zip-{new_id()}"
        tmpdir.mkdir(parents=True)
        try:
            for n in names:
                fname = Path(n).name
                if not converters.is_supported(fname) or fname.lower().endswith((".zip", ".ics")):
                    continue
                folder = str(Path(n).parent) if str(Path(n).parent) not in (".", "") else ""
                nb = notebook_id
                if folder and not notebook_id:
                    top = folder.split("/")[0]
                    if top not in folder_books:
                        folder_books[top] = _notebook_for(top)
                    nb = folder_books[top]
                target = tmpdir / f"{new_id(6)}-{safe_filename(fname)}"
                with z.open(n) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                try:
                    note, w = _note_from_file(target, fname, settings, nb, keep_original=False)
                    created.append(note["id"])
                    warnings += [f"{fname}: {x}" for x in w[:2]]
                except converters.ConversionError as exc:
                    warnings.append(f"{fname}: {exc}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return created, warnings


def _notebook_for(name: str) -> str:
    row = db.q1("SELECT id FROM notebooks WHERE lower(name) = lower(?)", (name,))
    if row:
        return row["id"]
    from .notebooks import create as create_nb

    return create_nb(name)["id"]


def process(job_id: str) -> None:
    row = db.q1("SELECT * FROM imports WHERE id = ?", (job_id,))
    if not row or row["status"] in ("done", "error"):
        return
    opts = jloads(row["options"], {})
    tmp = Path(opts.get("tmp", ""))
    settings = db.get_settings()
    filename = row["filename"]
    notebook_id = opts.get("notebook_id") or settings["import"].get("default_notebook") or None
    keep = opts.get("keep_original", settings["import"].get("keep_originals", True))
    ai_mode = opts.get("ai_mode") or settings["ai"].get("smart_import", "suggest")
    if not settings["ai"].get("enabled"):
        ai_mode = "off"
    ok = False
    try:
        if not tmp.exists():
            raise converters.ConversionError("The uploaded file went missing before it could be read.")
        _set(job_id, status="converting", message=None)
        ext = Path(filename).suffix.lower()
        if ext == ".ics":
            res = events_svc.import_ics(tmp.read_bytes(), calendar_id=opts.get("calendar_id"))
            _set(job_id, status="done", message=f"Added {res['created']} events, updated {res['updated']}.")
            ok = True
            return
        if ext == ".zip":
            created, warnings = _process_zip(tmp, settings, notebook_id, keep)
            msg = f"Imported {len(created)} notes." + (f" {len(warnings)} file(s) skipped." if warnings else "")
            if warnings and not created:
                msg = warnings[0]
            _set(job_id, status="done", message=msg, note_id=created[0] if len(created) == 1 else None)
            ok = True
            return
        note, warnings = _note_from_file(tmp, filename, settings, notebook_id, keep)
        _set(job_id, note_id=note["id"])
        message = " ".join(warnings[:2]) or None
        if ai_mode in ("suggest", "auto"):
            _set(job_id, status="ai", message="Local AI is reading the document…", note_id=note["id"])
            try:
                ai_msg, has_sug = _apply_ai(note, filename, settings, ai_mode)
                if has_sug:
                    opts["has_suggestions"] = True
                    with db.tx() as c:
                        c.execute("UPDATE imports SET options = ? WHERE id = ?", (json.dumps(opts), job_id))
                message = " ".join(x for x in (ai_msg, message) if x)
            except Exception as exc:
                message = " ".join(x for x in (f"Imported without AI ({exc}).", message) if x)
        _set(job_id, status="done", message=message, note_id=note["id"])
        ok = True
    except converters.ConversionError as exc:
        _set(job_id, status="error", message=str(exc))
    except Exception as exc:  # never let one bad file stop the queue
        _set(job_id, status="error", message=f"Import failed: {exc.__class__.__name__}: {exc}")
    finally:
        tmp.unlink(missing_ok=True)
        if opts.get("inbox_path"):
            _file_inbox_source(Path(opts["inbox_path"]), ok)


def _file_inbox_source(src: Path, ok: bool) -> None:
    if not src.exists():
        return
    dest_dir = src.parent / ("Imported" if ok else "Couldn't import")
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / src.name
    i = 2
    while dest.exists():
        dest = dest_dir / f"{src.stem} ({i}){src.suffix}"
        i += 1
    try:
        shutil.move(str(src), str(dest))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Watched Inbox folder
# ---------------------------------------------------------------------------
class InboxWatcher:
    """Polls the Inbox folder; a file is imported once its size has stopped changing."""

    def __init__(self) -> None:
        self.seen: dict[str, tuple[int, float]] = {}
        self.queued: set[str] = set()
        self.error: str | None = None
        self.folder: Path | None = None

    def scan(self) -> list[str]:
        settings = db.get_settings()["import"]
        if not settings.get("inbox_enabled"):
            self.folder = None
            return []
        folder = Path(settings.get("inbox_dir") or "~/Notes Inbox").expanduser()
        self.folder = folder
        try:
            folder.mkdir(parents=True, exist_ok=True)
            entries = list(folder.iterdir())
            self.error = None
        except OSError as exc:
            self.error = f"Can't use {folder}: {exc.strerror or exc}"
            return []
        started = []
        now = time.time()
        for p in entries:
            name = p.name
            low = name.lower()
            if not p.is_file() or name.startswith(".") or low.startswith(IGNORED_PREFIXES) or low.endswith(IGNORED_SUFFIXES):
                continue
            key = str(p)
            if key in self.queued:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime)
            if self.seen.get(key) == sig and now - st.st_mtime > 2 and st.st_size > 0:
                self.seen.pop(key, None)
                if not converters.is_supported(name):
                    _file_inbox_source(p, False)
                    continue
                self.queued.add(key)
                enqueue(name, p, origin="inbox", inbox_path=key)
                started.append(name)
            else:
                self.seen[key] = sig
        self.queued = {k for k in self.queued if Path(k).exists()}
        return started

    def status(self) -> dict:
        s = db.get_settings()["import"]
        folder = Path(s.get("inbox_dir") or "~/Notes Inbox").expanduser()
        return {"enabled": bool(s.get("inbox_enabled")), "folder": str(folder), "exists": folder.exists(), "error": self.error}


watcher = InboxWatcher()
