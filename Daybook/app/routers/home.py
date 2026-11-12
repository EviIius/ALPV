"""Today dashboard, unified search, quick capture, live updates."""
from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

from fastapi import APIRouter, Body, Request
from fastapi.responses import StreamingResponse

from .. import db
from ..bus import bus
from ..services import events, nlparse, notes, notify, tasks
from ..util import local_now, today_str

router = APIRouter(prefix="/api")


def _months_back(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - n, 12)
    m += 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def look_back(d: date) -> list[dict]:
    """Notes from this day a week, a month, a quarter, half a year and a year ago."""
    out = []
    for label, day in (("A week ago", d - timedelta(days=7)), ("A month ago", _months_back(d, 1)), ("3 months ago", _months_back(d, 3)),
                       ("6 months ago", _months_back(d, 6)), ("A year ago", _months_back(d, 12))):
        items = [n for n in notes.list_notes(view="all", linked_date=day.isoformat(), limit=6, sort="date")["items"] if not n.get("locked")]
        if items:
            out.append({"label": label, "date": day.isoformat(), "notes": items[:3], "more": max(0, len(items) - 3)})
    return out


@router.get("/today")
def today(day: str | None = None):
    d = date.fromisoformat(day) if day else date.fromisoformat(today_str())
    daily = notes.daily(d.isoformat(), create_if_missing=False)
    recent = notes.list_notes(view="all", limit=6)["items"]
    upcoming, seen = [], set()
    for o in events.occurrences_between(d + timedelta(days=1), d + timedelta(days=6)):
        if o["event_id"] in seen:  # show a repeating event once, at its next occurrence
            continue
        seen.add(o["event_id"])
        upcoming.append(o)
    upcoming = upcoming[:8]
    overdue = tasks.list_tasks("open", due_to=(d - timedelta(days=1)).isoformat())
    due_today = tasks.list_tasks("open", due_from=d.isoformat(), due_to=d.isoformat())
    return {
        "date": d.isoformat(),
        "now": local_now().strftime("%Y-%m-%dT%H:%M"),
        "events": events.occurrences_between(d, d),
        "upcoming": upcoming,
        "tasks": {"overdue": overdue, "today": due_today},
        "daily_note": {"id": daily["id"], "title": daily["title"]} if daily else None,
        "linked_notes": notes.list_notes(view="all", linked_date=d.isoformat(), limit=12, sort="date")["items"],
        "mentions": db.q("""SELECT m.date, m.kind, m.snippet, n.id AS note_id, n.title, n.linked_date FROM date_mentions m
                            JOIN notes n ON n.id = m.note_id
                            WHERE n.trashed_at IS NULL AND n.locked = 0 AND (m.date = ? OR (m.date < ? AND m.date_end >= ?))
                              AND (n.linked_date IS NULL OR n.linked_date != ?)
                            GROUP BY n.id ORDER BY m.kind = 'deadline' DESC, n.linked_date DESC LIMIT 8""",
                         (d.isoformat(), d.isoformat(), d.isoformat(), d.isoformat())),
        "needs_date": db.scalar("""SELECT count(*) FROM notes WHERE trashed_at IS NULL AND date_status IN ('suggested', 'review', 'none')
                                   AND (lib_path IS NOT NULL OR kind = 'import')""") or 0,
        "recent_notes": recent,
        "look_back": look_back(d),
        "counts": {"notes": notes.counts(), "tasks": tasks.counts()},
        "notifications": [n for n in notify.recent(10) if not n["read"]],
    }


@router.get("/search")
def search(q: str = ""):
    q = q.strip()
    if not q:
        return {"notes": [], "events": [], "tasks": [], "pages": [], "highlights": []}
    from ..services import reader

    return {"notes": notes.search(q, 8), "events": events.search(q, 6), "tasks": tasks.search(q, 6),
            "pages": reader.search_pages(q, 6), "highlights": reader.all_annotations(q, 5)}


@router.post("/quick/parse")
def quick_parse(payload: dict = Body(...)):
    text = (payload.get("text") or "").strip()
    if not text:
        raise ValueError("Type something to capture.")
    default = int(db.get_settings()["calendar"].get("default_duration", 60))
    return nlparse.parse(text, local_now().replace(tzinfo=None), default)


@router.post("/quick/create")
def quick_create(payload: dict = Body(...)):
    kind = payload.get("type")
    if kind == "event":
        d = payload.get("date") or today_str()
        all_day = bool(payload.get("all_day")) or not payload.get("start_time")
        start = d if all_day else f"{d}T{payload['start_time']}"
        end = d if all_day else f"{d}T{payload.get('end_time') or payload['start_time']}"
        cal_id = payload.get("calendar_id")
        if not cal_id:
            for tag in payload.get("tags") or []:
                cal = events.calendar_by_name(tag)
                if cal:
                    cal_id = cal["id"]
                    break
        reminders = payload.get("reminders")
        if reminders is None:
            reminders = [] if all_day else [int(db.get_settings()["notifications"].get("default_reminder", 15))]
        ev = events.create({"title": payload.get("title"), "start": start, "end": end, "all_day": all_day,
                            "location": payload.get("location") or "", "rrule": payload.get("rrule"),
                            "calendar_id": cal_id, "reminders": reminders})
        return {"type": "event", "item": ev}
    if kind == "task":
        tags = payload.get("tags") or []
        t = tasks.create({"title": payload.get("title"), "due_date": payload.get("date"),
                          "due_time": payload.get("start_time"), "priority": payload.get("priority") or 0,
                          "list": tags[0].capitalize() if tags else None, "repeat": payload.get("rrule")})
        return {"type": "task", "item": t}
    if kind == "note":
        n = notes.create(title=payload.get("title") or "", body=payload.get("body") or "", tags=payload.get("tags"))
        return {"type": "note", "item": n}
    raise ValueError("Choose event, task or note.")


@router.get("/notifications")
def notifications():
    return notify.recent(30)


@router.post("/notifications/read")
def notifications_read():
    notify.mark_read()
    return {"ok": True}


@router.get("/stream")
async def stream(request: Request):
    """Server-Sent Events: live sync between devices, reminders, import progress."""
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 4000\n\n"
            yield f"data: {json.dumps({'type': 'hello'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
