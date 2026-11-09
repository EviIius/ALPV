"""Library (ALPV) endpoints: status, scanning, dates, the review queue, files,
timeline, decisions, follow-ups, people, mockups, versions and local-AI helpers."""
from __future__ import annotations

import difflib
import json
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from anyio import to_thread
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse

from .. import db, security
from ..bus import changed
from ..services import ai_tasks, attachments, datescan, library, notes, tasks
from ..services.ai import AIClient, AIError, pick_models, run_sync
from ..util import jloads, md_to_text, now_ms, today_str

router = APIRouter(prefix="/api/library")
engine = library.engine


def _root() -> Path:
    root = engine.root()
    if root is None:
        raise HTTPException(409, "No library folder is connected. Choose one in Settings → Library.")
    return root


def _local_only(request: Request) -> None:
    if security.classify_ip(request.state.client_ip) != "loopback" or sys.platform != "darwin":
        raise HTTPException(400, "This opens the file on the Mac running Daybook, so use Daybook on that Mac.")


# ---------------------------------------------------------------------------
# Status and scanning
# ---------------------------------------------------------------------------
@router.get("/status")
def status():
    return engine.status()


@router.post("/rescan")
async def rescan(payload: dict = Body(default_factory=dict)):
    _root()
    if payload.get("wait", True):
        res = await to_thread.run_sync(lambda: engine.scan(full=True, wait=True))
        return {**res, "status": engine.status()}
    import threading

    threading.Thread(target=engine.scan, kwargs={"full": True}, daemon=True).start()
    return {"started": True}


@router.get("/git")
def git():
    return engine.git_status()


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
MOCKUP_CSP = ("sandbox allow-scripts allow-forms allow-modals allow-downloads; default-src 'none'; "
              "script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; "
              "media-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'")
INLINE_CSP = "default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'"


@router.get("/file")
def file(path: str, download: bool = False):
    root = _root()
    row = db.q1("SELECT path, kind, status FROM lib_files WHERE path = ?", (path,))
    if not row or row["status"] not in ("ok", "error"):
        raise HTTPException(404, "That file isn't in your library.")
    target = library.safe_rel(root, path)
    if not target.is_file():
        raise HTTPException(404, "That file is missing from the folder.")
    mime = attachments.guess_mime(target.name)
    headers = {"Content-Disposition": f"{'attachment' if download else 'inline'}; filename*=UTF-8''{quote(target.name)}",
               "Cache-Control": "no-store"}
    if not download and row["kind"] == "mockup":
        headers.update({"Content-Security-Policy": MOCKUP_CSP, "X-Frame-Options": "SAMEORIGIN"})
        return FileResponse(target, media_type="text/html; charset=utf-8", headers=headers)
    if not download and (mime in attachments.INLINE_SAFE or mime == "image/svg+xml"):
        headers.update({"Content-Security-Policy": INLINE_CSP + ("; sandbox" if mime == "image/svg+xml" else ""),
                        "X-Frame-Options": "SAMEORIGIN"})
        return FileResponse(target, media_type=mime, headers=headers)
    return FileResponse(target, media_type="application/octet-stream", headers={**headers, "Content-Disposition":
                        f"attachment; filename*=UTF-8''{quote(target.name)}"})


def _note_path(note_id: str) -> Path:
    row = notes.get_row(note_id)
    if not row.get("lib_path"):
        raise HTTPException(400, "This note isn't a file in your library.")
    return library.safe_rel(_root(), row["lib_path"])


@router.post("/open/{note_id}")
def open_file(note_id: str, request: Request):
    """Open the original in its app (Word, PowerPoint, a browser for mockups)."""
    _local_only(request)
    p = _note_path(note_id)
    subprocess.run(["open", str(p)], timeout=10)
    return {"ok": True}


@router.post("/reveal/{note_id}")
def reveal(note_id: str, request: Request):
    _local_only(request)
    p = _note_path(note_id)
    subprocess.run(["open", "-R", str(p)], timeout=10)
    return {"ok": True}


@router.post("/reveal-folder")
def reveal_folder(request: Request):
    _local_only(request)
    subprocess.run(["open", str(_root())], timeout=10)
    return {"ok": True}


@router.post("/folders")
def new_folder(payload: dict = Body(...)):
    root = _root()
    name = (payload.get("name") or "").strip()
    from ..util import safe_filename

    clean = safe_filename(name, "")
    if not clean or clean.startswith("."):
        raise HTTPException(400, "Give the folder a name.")
    (root / clean).mkdir(exist_ok=True)
    nb = library.notebook_for(clean, root.name)
    return db.q1("SELECT * FROM notebooks WHERE id = ?", (nb,))


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
def _review_item(r: dict) -> dict:
    info = jloads(r.get("date_info"), {}) or {}
    return {
        "id": r["id"], "title": r["title"], "lib_path": r.get("lib_path"), "lib_kind": r.get("lib_kind"),
        "linked_date": r.get("linked_date"), "status": r.get("date_status"), "confidence": r.get("date_confidence"),
        "source": r.get("date_source"), "suggestion": info.get("suggestion"), "candidates": info.get("candidates") or [],
        "ai": info.get("ai"), "conflict": info.get("conflict"), "evidence": info.get("evidence"),
        "snippet": md_to_text(r.get("head") or "")[:220],
    }


@router.get("/review")
def review():
    return [_review_item(r) for r in engine.needs_review()]


def _ai_inputs(note_id: str) -> dict:
    row = notes.get_row(note_id)
    if row["locked"]:
        raise HTTPException(423, "Locked notes are never sent to AI.")
    info = jloads(row.get("date_info"), {}) or {}
    ments = db.q("SELECT date, date_end AS end, kind, snippet FROM date_mentions WHERE note_id = ? ORDER BY date LIMIT 20", (note_id,))
    return {"row": row, "info": info, "mentions": ments, "head": md_to_text(row["body"] or "")[:4000]}


def _verify(pick: dict, inp: dict) -> bool:
    d = pick.get("date")
    if not d:
        return False
    if d in {c.get("date") for c in inp["info"].get("candidates") or []} | {m["date"] for m in inp["mentions"]}:
        return True
    row = inp["row"]
    blob = " ".join([row.get("lib_path") or "", row["title"] or "", inp["head"]])
    return any(f.start.isoformat() == d for f in datescan.find_dates(blob, date.fromisoformat(d), filename=True))


async def ai_date_one(note_id: str, client, models, *, apply: bool) -> dict:
    inp = await to_thread.run_sync(_ai_inputs, note_id)
    row = inp["row"]
    pick = await ai_tasks.pick_date(client, models, path=row.get("lib_path") or row.get("source_name") or "",
                                    title=row["title"], head=inp["head"], clues=inp["info"].get("candidates") or [],
                                    mentions=inp["mentions"], today=today_str())
    verified = _verify(pick, inp)
    pick.update({"verified": verified, "at": now_ms(), "hash": notes.content_hash(row["title"], row["body"] or "")})

    def store():
        cur = notes.get_row(note_id)
        info = jloads(cur.get("date_info"), {}) or {}
        info["ai"] = pick
        with db.tx() as c:
            c.execute("UPDATE notes SET date_info = ? WHERE id = ?", (json.dumps(info), note_id))
        if apply and verified and pick["date"] and pick["confidence"] >= 0.6:
            engine.set_date(note_id, day=pick["date"], end=pick.get("end"), by="ai", note=pick.get("reason"))
            pick["applied"] = True
        changed("notes", note_id)
    await to_thread.run_sync(store)
    return {"id": note_id, **pick}


async def _ai_ready():
    settings = await to_thread.run_sync(db.get_settings)
    if not settings["ai"].get("enabled"):
        raise HTTPException(400, "Local AI is turned off. Turn it on in Settings → Local AI.")
    client = AIClient.from_settings(settings)
    models = pick_models(settings, await client.models())
    if not (models.get("fast") or models.get("smart")):
        raise HTTPException(400, "Install a chat model in your local AI app first.")
    return settings, client, models


@router.post("/dates/ai")
async def ai_dates(payload: dict = Body(default_factory=dict)):
    """Let the local AI date documents in the review queue. `apply` writes verified picks to dates.json."""
    settings, client, models = await _ai_ready()
    ids = payload.get("ids") or [r["id"] for r in await to_thread.run_sync(engine.needs_review)][: int(payload.get("limit") or 8)]
    apply = bool(payload.get("apply", settings["library"].get("ai_dates") == "auto"))
    out = []
    for nid in ids[:25]:
        try:
            out.append(await ai_date_one(nid, client, models, apply=apply))
        except (AIError, HTTPException) as exc:
            out.append({"id": nid, "error": getattr(exc, "detail", None) or str(exc)})
    return out


@router.post("/dates/{note_id}")
async def set_date(note_id: str, payload: dict = Body(...)):
    """{date, end?, time?} sets a date; {none: true} marks it undated; {clear: true} goes back to automatic;
    {accept: true} takes the current suggestion (or the local AI's pick with accept: 'ai')."""
    row = await to_thread.run_sync(notes.get_row, note_id)
    info = jloads(row.get("date_info"), {}) or {}
    by = payload.get("by") if payload.get("by") in ("you", "claude", "ai") else "you"
    if payload.get("accept"):
        pick = info.get("ai") if payload["accept"] == "ai" else info.get("suggestion")
        if not pick or not pick.get("date"):
            raise HTTPException(400, "There's no suggestion to accept.")
        res = await to_thread.run_sync(lambda: engine.set_date(
            note_id, day=pick["date"], end=pick.get("end"), time_=pick.get("time"),
            by="ai" if payload["accept"] == "ai" else by, note=pick.get("reason")))
    elif payload.get("clear"):
        res = await to_thread.run_sync(lambda: engine.set_date(note_id, clear=True))
    elif payload.get("none"):
        res = await to_thread.run_sync(lambda: engine.set_date(note_id, none=True, by=by, note=payload.get("note")))
    else:
        day = payload.get("date")
        if not day:
            raise HTTPException(400, "Pick a date.")
        res = await to_thread.run_sync(lambda: engine.set_date(note_id, day=day, end=payload.get("end") or None,
                                                               time_=payload.get("time") or None, by=by,
                                                               note=payload.get("note")))
    return res


def ai_tick() -> int:
    """Background: in 'suggest' or 'auto' mode, the local AI looks at up to two undated files a minute."""
    settings = db.get_settings()
    mode = settings["library"].get("ai_dates", "suggest")
    if mode == "off" or not settings["ai"].get("enabled") or engine.root() is None:
        return 0
    todo = []
    for r in engine.needs_review(60):
        full = db.q1("SELECT title, body, date_info FROM notes WHERE id = ?", (r["id"],))
        ai = (jloads(full["date_info"], {}) or {}).get("ai")
        if not ai or ai.get("hash") != notes.content_hash(full["title"], full["body"] or ""):
            todo.append(r["id"])
        if len(todo) >= 2:
            break
    if not todo:
        return 0
    client = AIClient.from_settings(settings)
    try:
        models = pick_models(settings, run_sync(client.models()))
    except AIError:
        return 0
    if not (models.get("fast") or models.get("smart")):
        return 0
    done = 0
    for nid in todo:
        try:
            run_sync(ai_date_one(nid, client, models, apply=(mode == "auto")))
            done += 1
        except Exception:
            break
    return done


# ---------------------------------------------------------------------------
# Calendar data, timeline and mentions
# ---------------------------------------------------------------------------
def _dated(first: str, last: str, *, folder: str | None = None, kind: str | None = None, person: str | None = None) -> list[dict]:
    where = ["n.trashed_at IS NULL", "n.linked_date IS NOT NULL", "n.kind != 'daily'",
             "n.linked_date <= ?", "COALESCE(n.date_end, n.linked_date) >= ?"]
    params: list = [last, first]
    if folder:
        where.append("n.notebook_id = ?")
        params.append(folder)
    if kind:
        where.append("n.lib_kind = ?")
        params.append(kind)
    if person:
        where.append("EXISTS (SELECT 1 FROM json_each(n.facts, '$.people') p WHERE lower(p.value) = lower(?))")
        params.append(person)
    rows = db.q(f"""SELECT n.id, n.title, n.linked_date, n.date_end, n.date_time, n.date_source, n.date_status, n.lib_path,
                           n.lib_kind, n.kind, n.notebook_id, n.locked, b.name AS notebook, b.color AS color, n.facts
                    FROM notes n LEFT JOIN notebooks b ON b.id = n.notebook_id
                    WHERE {' AND '.join(where)} ORDER BY n.linked_date, n.date_time IS NULL, n.date_time, lower(n.title)""",
                tuple(params))
    for r in rows:
        f = jloads(r.pop("facts"), None) or {}
        r["people"] = (f.get("people") or [])[:8]
        r["actions"] = len([a for a in f.get("actions") or [] if not a.get("done")])
        r["decisions"] = len(f.get("decisions") or [])
    return rows


@router.get("/calendar")
def calendar(start: str, end: str):
    """Dated notes and files, plus how often each day is mentioned, for the calendar."""
    date.fromisoformat(start), date.fromisoformat(end)
    items = _dated(start, end)
    mentions = db.q("""SELECT m.date, count(DISTINCT m.note_id) AS notes, sum(m.kind = 'deadline') AS deadlines
                       FROM date_mentions m JOIN notes n ON n.id = m.note_id
                       WHERE n.trashed_at IS NULL AND m.date BETWEEN ? AND ? AND (n.linked_date IS NULL OR m.date != n.linked_date)
                       GROUP BY m.date""", (start, end))
    return {"items": items, "mentions": {m["date"]: {"notes": m["notes"], "deadlines": m["deadlines"] or 0} for m in mentions}}


@router.get("/mentions")
def mentions(day: str):
    date.fromisoformat(day)
    rows = db.q("""SELECT m.date, m.date_end, m.kind, m.snippet, n.id AS note_id, n.title, n.linked_date
                   FROM date_mentions m JOIN notes n ON n.id = m.note_id
                   WHERE n.trashed_at IS NULL AND (m.date = ? OR (m.date < ? AND m.date_end >= ?))
                   ORDER BY m.kind = 'deadline' DESC, n.linked_date DESC""", (day, day, day))
    seen, out = set(), []
    for r in rows:
        if r["note_id"] in seen or r["linked_date"] == day:
            continue
        seen.add(r["note_id"])
        out.append(r)
    return out


@router.get("/timeline")
def timeline(start: str | None = None, end: str | None = None, folder: str | None = None, kind: str | None = None,
             person: str | None = None):
    last = end or (date.fromisoformat(today_str()) + timedelta(days=60)).isoformat()
    first = start or (date.fromisoformat(last) - timedelta(days=120)).isoformat()
    return {"start": first, "end": last, "items": _dated(first, last, folder=folder, kind=kind, person=person)}


# ---------------------------------------------------------------------------
# Meeting facts across the library: decisions, follow-ups, people
# ---------------------------------------------------------------------------
def _facts_rows() -> list[dict]:
    return db.q("""SELECT n.id, n.title, n.linked_date, n.date_time, n.facts, n.lib_path, b.name AS notebook
                   FROM notes n LEFT JOIN notebooks b ON b.id = n.notebook_id
                   WHERE n.trashed_at IS NULL AND n.facts IS NOT NULL
                   ORDER BY n.linked_date DESC, n.date_time DESC""")


@router.get("/decisions")
def decisions(q: str | None = None, person: str | None = None):
    out = []
    for r in _facts_rows():
        f = jloads(r["facts"], {}) or {}
        if person and person.lower() not in [p.lower() for p in f.get("people") or []]:
            continue
        for d in f.get("decisions") or []:
            if q and q.lower() not in d.lower():
                continue
            out.append({"text": d, "note_id": r["id"], "title": r["title"], "date": r["linked_date"], "notebook": r["notebook"]})
    return out[:500]


@router.get("/followups")
def followups(owner: str | None = None, show: str = "open"):
    tracked = {(t["note_id"], t["title"].strip().lower()) for t in db.q("SELECT note_id, title FROM tasks WHERE note_id IS NOT NULL")}
    out = []
    for r in _facts_rows():
        f = jloads(r["facts"], {}) or {}
        for i, a in enumerate(f.get("actions") or []):
            if owner and owner.lower() not in (a.get("owner") or "").lower():
                continue
            done = bool(a.get("done"))
            is_tracked = (r["id"], a["text"].strip().lower()) in tracked
            if show == "open" and done:
                continue
            out.append({**a, "index": i, "note_id": r["id"], "title": r["title"], "date": r["linked_date"],
                        "notebook": r["notebook"], "tracked": is_tracked})
    return out[:800]


@router.post("/followups/track")
def track(payload: dict = Body(...)):
    note_id = payload.get("note_id")
    row = notes.get_row(note_id)
    f = jloads(row.get("facts"), {}) or {}
    try:
        a = (f.get("actions") or [])[int(payload.get("index"))]
    except (IndexError, TypeError, ValueError):
        raise HTTPException(404, "That action item changed. Refresh and try again.")
    title = a["text"] + (f" ({a['owner']})" if a.get("owner") and payload.get("with_owner") else "")
    t = tasks.create({"title": a["text"], "details": f"From {row['title']}" + (f" — owner: {a['owner']}" if a.get("owner") else ""),
                      "due_date": a.get("due"), "note_id": note_id, "list": "Follow-ups"})
    return t


@router.get("/people")
def people():
    counts: dict[str, dict] = {}
    for r in _facts_rows():
        f = jloads(r["facts"], {}) or {}
        for p in f.get("people") or []:
            key = p.lower()
            e = counts.setdefault(key, {"name": p, "meetings": 0, "last": None, "first": None, "open_actions": 0})
            e["meetings"] += 1
            if r["linked_date"]:
                e["last"] = max(e["last"] or "", r["linked_date"])
                e["first"] = min(e["first"] or "9999", r["linked_date"])
        for a in f.get("actions") or []:
            for p in (a.get("owner") or "").replace("/", ",").split(","):
                k = p.strip().lower()
                if k in counts and not a.get("done"):
                    counts[k]["open_actions"] += 1
    return sorted(counts.values(), key=lambda e: (-e["meetings"], e["name"]))


@router.get("/mockups")
def mockups():
    return db.q("""SELECT n.id, n.title, n.linked_date, n.lib_path, n.lib_kind, b.name AS notebook FROM notes n
                   LEFT JOIN notebooks b ON b.id = n.notebook_id
                   WHERE n.trashed_at IS NULL AND n.lib_kind IN ('mockup', 'image')
                   ORDER BY n.linked_date DESC, lower(n.title)""")


# ---------------------------------------------------------------------------
# Versions: compare two documents
# ---------------------------------------------------------------------------
def _diff(a_id: str, b_id: str) -> dict:
    a, b = notes.get_row(a_id), notes.get_row(b_id)
    if a["locked"] or b["locked"]:
        raise HTTPException(423, "Locked notes can't be compared.")
    la = [l for l in md_to_text(a["body"]).splitlines() if l.strip()]
    lb = [l for l in md_to_text(b["body"]).splitlines() if l.strip()]
    lines, added, removed = [], 0, 0
    for op in difflib.unified_diff(la, lb, lineterm="", n=1):
        if op.startswith(("---", "+++")):
            continue
        if op.startswith("@@"):
            lines.append({"op": "gap", "text": "…"})
        elif op.startswith("+"):
            added += 1
            lines.append({"op": "add", "text": op[1:]})
        elif op.startswith("-"):
            removed += 1
            lines.append({"op": "del", "text": op[1:]})
        else:
            lines.append({"op": "same", "text": op[1:]})
    return {"a": {"id": a_id, "title": a["title"], "date": a["linked_date"]},
            "b": {"id": b_id, "title": b["title"], "date": b["linked_date"]},
            "added": added, "removed": removed, "lines": lines[:3000],
            "similarity": round(difflib.SequenceMatcher(None, "\n".join(la), "\n".join(lb)).quick_ratio(), 3)}


@router.get("/diff")
def diff(a: str, b: str):
    return _diff(a, b)


@router.post("/diff/summary")
async def diff_summary(payload: dict = Body(...)):
    d = await to_thread.run_sync(_diff, payload.get("a"), payload.get("b"))
    _s, client, models = await _ai_ready()
    text = "\n".join(("+ " if l["op"] == "add" else "- " if l["op"] == "del" else "  ") + l["text"]
                     for l in d["lines"] if l["op"] in ("add", "del"))
    if not text.strip():
        return {"markdown": "These two versions have the same text.", "model": None, "ms": 0}
    return await ai_tasks.diff_summary(client, models, old_title=d["a"]["title"], new_title=d["b"]["title"], diff_text=text)


# ---------------------------------------------------------------------------
# Local AI: transcript -> meeting notes, weekly review
# ---------------------------------------------------------------------------
@router.post("/transcript-notes/{note_id}")
async def transcript_notes(note_id: str, payload: dict = Body(default_factory=dict)):
    row = await to_thread.run_sync(notes.get_row, note_id)
    if row["locked"]:
        raise HTTPException(423, "Locked notes are never sent to AI.")
    if not md_to_text(row["body"] or "").strip():
        raise HTTPException(400, "This recording has no text.")
    _s, client, models = await _ai_ready()
    folder = library.folder_of(row["lib_path"]) if row.get("lib_path") else ""
    res = await ai_tasks.transcript_to_notes(client, models, title=row["title"], transcript=row["body"],
                                             day=row.get("linked_date"), time_=row.get("date_time"), folder=folder,
                                             today=today_str())
    md = res["markdown"]
    title = md.splitlines()[0].lstrip("# ").strip() if md.startswith("#") else f"Notes from {row['title']}"
    body = "\n".join(md.splitlines()[1:]).lstrip("\n") if md.startswith("#") else md
    if payload.get("preview"):
        return {"title": title, "markdown": body, "model": res["model"], "ms": res["ms"]}

    def make():
        nb = row["notebook_id"]
        day = row.get("linked_date")
        name = f"{title} {day}" if day and day not in title else title
        n = notes.create(title=name, body=body + f"\n\n---\n*Drafted by {res['model']} on this computer from the recording "
                         f"“{row['title']}”.*\n", notebook_id=nb)
        if day:
            engine.set_date(n["id"], day=day, time_=row.get("date_time"), by="ai", note="Same day as the recording")
        return notes.get(n["id"])
    return await to_thread.run_sync(make)


@router.post("/week-review")
async def week_review(payload: dict = Body(default_factory=dict)):
    start = date.fromisoformat(payload.get("start") or today_str())
    start = start - timedelta(days=start.weekday())
    end = start + timedelta(days=6)

    def context() -> str:
        items = _dated(start.isoformat(), end.isoformat())
        lines = []
        for it in items[:40]:
            n = notes.get_row(it["id"])
            if n["locked"]:
                continue
            f = jloads(n.get("facts"), {}) or {}
            lines.append(f"## {it['linked_date']} — {it['title']} ({library.KIND_LABELS.get(it['lib_kind'] or '', 'Note')})")
            if f.get("summary"):
                lines.append(f["summary"])
            else:
                lines.append(md_to_text(n["body"] or "")[:900])
            for d in (f.get("decisions") or [])[:6]:
                lines.append(f"- Decision: {d}")
            for a in (f.get("actions") or [])[:8]:
                lines.append(f"- Action ({a.get('owner') or 'unassigned'}): {a['text']}")
        later = db.q("""SELECT DISTINCT m.date, m.snippet FROM date_mentions m JOIN notes n ON n.id = m.note_id
                        WHERE n.trashed_at IS NULL AND m.date > ? AND m.date <= ? AND n.linked_date BETWEEN ? AND ?
                        ORDER BY m.date LIMIT 15""",
                     (end.isoformat(), (end + timedelta(days=45)).isoformat(), start.isoformat(), end.isoformat()))
        if later:
            lines.append("\n## Dates mentioned for later")
            lines += [f"- {m['date']}: {m['snippet'][:160]}" for m in later]
        return "\n".join(lines)[:24000]
    ctx = await to_thread.run_sync(context)
    if not ctx.strip():
        raise HTTPException(400, "Nothing is dated in that week yet.")
    _s, client, models = await _ai_ready()
    label = f"the week of {start.strftime('%B')} {start.day}, {start.year}"
    res = await ai_tasks.week_review(client, models, week_label=label, context=ctx)
    return {**res, "start": start.isoformat(), "end": end.isoformat(), "label": label}
