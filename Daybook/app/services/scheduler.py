"""Background work: reminders, the Inbox folder, imports, the AI index and
housekeeping. Everything runs inside the server process — no cron needed."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from .. import db, security
from ..util import jloads, now_ms, tzinfo
from . import backup, events as events_svc, importer, notes as notes_svc, notify

log = logging.getLogger("daybook")
GRACE = timedelta(minutes=20)


def _when(minutes: int) -> str:
    if minutes <= 0:
        return "Starting now"
    if minutes < 60:
        return f"In {minutes} min"
    if minutes < 1440:
        h = minutes // 60
        return f"In {h} hour{'s' if h > 1 else ''}"
    d = minutes // 1440
    return "Tomorrow" if d == 1 else f"In {d} days"


def _clock(dt: datetime, fmt: str) -> str:
    if fmt == "24h":
        return dt.strftime("%H:%M")
    return f"{(dt.hour % 12) or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _logged(key: str) -> bool:
    return db.q1("SELECT 1 FROM reminder_log WHERE key = ?", (key,)) is not None


def _log(key: str) -> None:
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO reminder_log(key, sent_at) VALUES (?, ?)", (key, now_ms()))


def check_reminders(now: datetime | None = None) -> int:
    settings = db.get_settings()
    fmt = settings["appearance"].get("time_format", "12h")
    now = now or datetime.now(tzinfo(settings["timezone"])).replace(tzinfo=None, second=0, microsecond=0)
    sent = 0
    cals = {c["id"]: c for c in events_svc.calendars()}
    for ev in db.q("SELECT * FROM events WHERE reminders != '[]'"):
        offsets = [int(x) for x in jloads(ev["reminders"], [])]
        if not offsets:
            continue
        horizon = now + timedelta(minutes=max(offsets)) + timedelta(days=1)
        for occ in events_svc.expand(ev, now - GRACE - timedelta(days=1), horizon, cals):
            start = events_svc.parse_dt(occ["start"], occ["all_day"])
            if occ["all_day"]:
                start = start.replace(hour=9)
            for m in offsets:
                fire = start - timedelta(minutes=m)
                if not (now - GRACE <= fire <= now):
                    continue
                key = f"e:{ev['id']}:{occ['start']}:{m}"
                if _logged(key):
                    continue
                when = "All day" if occ["all_day"] else _clock(start, fmt)
                body = f"{_when(m)} ({when})." + (f" {occ['location']}" if occ["location"] else "")
                notify.send(occ["title"], body, kind="event", ref=f"#/calendar/{occ['start'][:10]}",
                            redacted_body=f"An event is coming up: {_when(m).lower()} ({when}).")
                _log(key)
                sent += 1
    for t in db.q("SELECT * FROM tasks WHERE done = 0 AND remind IS NOT NULL AND due_date IS NOT NULL"):
        due = datetime.fromisoformat(f"{t['due_date']}T{t['due_time'] or '09:00'}")
        fire = due - timedelta(minutes=int(t["remind"]))
        if not (now - GRACE <= fire <= now):
            continue
        key = f"t:{t['id']}:{t['due_date']}:{t['due_time']}:{t['remind']}"
        if _logged(key):
            continue
        notify.send(t["title"], f"Task due {_when(int(t['remind'])).lower()} ({_clock(due, fmt)}).", kind="task",
                    ref="#/tasks", redacted_body="A task is due soon.")
        _log(key)
        sent += 1
    return sent


def housekeeping() -> None:
    security.purge_expired_sessions()
    notes_svc.empty_trash(older_than_days=notes_svc.TRASH_DAYS)
    with db.tx() as c:
        c.execute("DELETE FROM reminder_log WHERE sent_at < ?", (now_ms() - 90 * 86400 * 1000,))
        c.execute("DELETE FROM imports WHERE created_at < ? AND status IN ('done', 'error')", (now_ms() - 60 * 86400 * 1000,))
        c.execute("DELETE FROM lib_files WHERE note_id IS NULL AND status IN ('missing', 'trashed')")
    # Clean temp files left by interrupted imports (older than a day).
    tmp = db.get_paths().tmp
    for f in tmp.glob("*"):
        try:
            if f.is_file() and time.time() - f.stat().st_mtime > 86400 and not importer.pending_job_ids():
                f.unlink()
        except OSError:
            pass
    try:
        from . import reader

        reader.prune_cache()
    except Exception:
        log.exception("Couldn't tidy the slide cache")
    s = db.get_settings()["backups"]
    if s.get("auto") and now_ms() - int(s.get("last") or 0) > 20 * 3600 * 1000:
        backup.snapshot()


def index_some(limit: int = 6) -> int:
    from .ai import AIClient, AIError, pick_models, run_sync
    from . import embeddings

    settings = db.get_settings()
    ai = settings["ai"]
    if not (ai.get("enabled") and ai.get("auto_index")):
        return 0
    client = AIClient.from_settings(settings)
    try:
        models = pick_models(settings, run_sync(client.models()))
    except AIError:
        return 0
    model = models["embed"]
    if not model:
        return 0
    done = 0
    for note in embeddings.pending(model, limit=limit):
        chunks = embeddings.chunk_note(note["title"], note["body"])
        if not chunks:
            continue
        try:
            vectors = run_sync(client.embed(chunks, model=model))
        except AIError:
            break
        embeddings.store(note, model, chunks, vectors)
        done += 1
    return done


def library_poll() -> None:
    from .library import engine

    engine.poll()


def reader_index() -> None:
    from . import reader

    reader.index_pending(limit=8)


def glossary_scan() -> None:
    from . import glossary

    glossary.rebuild()


def library_ai() -> None:
    from ..routers.library import ai_tick

    ai_tick()


class Scheduler:
    def __init__(self) -> None:
        self.tasks: list[asyncio.Task] = []
        self.queue: asyncio.Queue[str] | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def enqueue(self, job_id: str) -> None:
        if self.loop and self.queue is not None:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, job_id)

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        importer.set_queue(self.enqueue)
        for job_id in await asyncio.to_thread(importer.pending_job_ids):
            self.queue.put_nowait(job_id)
        self.tasks = [
            asyncio.create_task(self._every(30, check_reminders, "reminders")),
            asyncio.create_task(self._every(4, importer.watcher.scan, "inbox")),
            asyncio.create_task(self._every(45, index_some, "index", initial_delay=20)),
            asyncio.create_task(self._every(5, library_poll, "library", initial_delay=1)),
            asyncio.create_task(self._every(60, library_ai, "library-ai", initial_delay=120)),
            asyncio.create_task(self._every(75, reader_index, "doc-pages", initial_delay=12)),
            asyncio.create_task(self._every(600, glossary_scan, "glossary", initial_delay=40)),
            asyncio.create_task(self._every(3600, housekeeping, "housekeeping", initial_delay=60)),
            asyncio.create_task(self._import_worker()),
        ]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self.tasks = []
        importer.set_queue(None)

    async def _every(self, seconds: float, fn, name: str, initial_delay: float = 2) -> None:
        await asyncio.sleep(initial_delay)
        while True:
            try:
                await asyncio.to_thread(fn)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Background job %s failed", name)
            await asyncio.sleep(seconds)

    async def _import_worker(self) -> None:
        assert self.queue is not None
        while True:
            job_id = await self.queue.get()
            try:
                await asyncio.to_thread(importer.process, job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Import %s failed", job_id)


scheduler = Scheduler()
