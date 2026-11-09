"""Tasks: quick to-dos with optional due date, priority, list and reminder."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .. import db
from ..bus import changed
from ..util import local_now, new_id, now_ms, today_str

PRIORITY = {"none": 0, "low": 1, "medium": 2, "med": 2, "high": 3}


class NotFound(Exception):
    pass


def to_api(r: dict) -> dict:
    out = dict(r)
    out["done"] = bool(r["done"])
    return out


def _validate(data: dict, partial: bool = False) -> dict:
    v: dict = {}
    if "title" in data or not partial:
        title = (data.get("title") or "").strip()
        if not title:
            raise ValueError("Add a title.")
        v["title"] = title[:300]
    if "details" in data:
        v["details"] = (data.get("details") or "")[:20000]
    if "list" in data:
        lst = (data.get("list") or "").strip()[:60]
        v["list"] = lst or None
    if "priority" in data:
        p = data.get("priority")
        p = PRIORITY.get(str(p).lower(), p) if isinstance(p, str) else p
        v["priority"] = max(0, min(3, int(p or 0)))
    if "due_date" in data:
        d = data.get("due_date") or None
        if d:
            date.fromisoformat(d)
        v["due_date"] = d
    if "due_time" in data:
        t = data.get("due_time") or None
        if t and not re.fullmatch(r"\d{2}:\d{2}", t):
            raise ValueError("Time must be HH:MM.")
        v["due_time"] = t
    if "remind" in data:
        r = data.get("remind")
        v["remind"] = None if r in (None, "", "none") else max(0, int(r))
    if "note_id" in data:
        nid = data.get("note_id") or None
        if nid and not db.q1("SELECT id FROM notes WHERE id = ?", (nid,)):
            nid = None
        v["note_id"] = nid
    if "done" in data:
        v["done"] = 1 if data.get("done") else 0
        v["done_at"] = now_ms() if data.get("done") else None
    return v


def get(task_id: str) -> dict:
    r = db.q1("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not r:
        raise NotFound(task_id)
    return to_api(r)


def create(data: dict, emit: bool = True) -> dict:
    v = _validate(data)
    tid = new_id()
    ts = now_ms()
    v.setdefault("details", "")
    sort = (db.scalar("SELECT min(sort) FROM tasks") or 0) - 1
    cols = ["id", "sort", "created_at", "updated_at", *v.keys()]
    with db.tx() as c:
        c.execute(
            f"INSERT INTO tasks({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            (tid, sort, ts, ts, *v.values()),
        )
    if emit:
        changed("tasks", tid)
    return get(tid)


def update(task_id: str, data: dict) -> dict:
    get(task_id)
    v = _validate(data, partial=True)
    if not v:
        return get(task_id)
    v["updated_at"] = now_ms()
    with db.tx() as c:
        c.execute(f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in v)} WHERE id = ?", (*v.values(), task_id))
    changed("tasks", task_id)
    return get(task_id)


def delete(task_id: str) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        c.execute("UPDATE events SET task_id = NULL WHERE task_id = ?", (task_id,))  # the time stays on the calendar
    changed("tasks", task_id)


def list_tasks(view: str = "open", list_name: str | None = None, due_from: str | None = None, due_to: str | None = None) -> list[dict]:
    today = today_str()
    where, params = [], []
    if view == "done":
        where.append("done = 1")
    elif view == "all":
        pass
    else:
        where.append("done = 0")
    if view == "today":
        where.append("due_date IS NOT NULL AND due_date <= ?")
        params.append(today)
    elif view == "upcoming":
        where.append("due_date IS NOT NULL AND due_date > ?")
        params.append(today)
    elif view == "someday":
        where.append("due_date IS NULL")
    if list_name:
        where.append("list = ?")
        params.append(list_name)
    if due_from:
        where.append("due_date >= ?")
        params.append(due_from)
    if due_to:
        where.append("due_date <= ?")
        params.append(due_to)
    order = "done_at DESC" if view == "done" else "CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date, CASE WHEN due_time IS NULL THEN 1 ELSE 0 END, due_time, priority DESC, sort"
    # The next time set aside for each task on the calendar.
    sql = (f"SELECT tasks.*, (SELECT min(e.start) FROM events e WHERE e.task_id = tasks.id AND e.end >= ?) AS blocked_at "
           f"FROM tasks {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY {order} LIMIT 500")
    return [to_api(r) for r in db.q(sql, (local_now().strftime("%Y-%m-%dT%H:%M"), *params))]


def lists() -> list[dict]:
    return db.q(
        "SELECT list AS name, sum(CASE WHEN done = 0 THEN 1 ELSE 0 END) AS open, count(*) AS total FROM tasks WHERE list IS NOT NULL GROUP BY list ORDER BY list"
    )


def counts() -> dict:
    today = today_str()
    row = db.q1(
        """SELECT sum(CASE WHEN done = 0 THEN 1 ELSE 0 END) AS open,
                  sum(CASE WHEN done = 0 AND due_date < ? THEN 1 ELSE 0 END) AS overdue,
                  sum(CASE WHEN done = 0 AND due_date = ? THEN 1 ELSE 0 END) AS today,
                  sum(CASE WHEN done = 0 AND due_date > ? THEN 1 ELSE 0 END) AS upcoming,
                  sum(CASE WHEN done = 0 AND due_date IS NULL THEN 1 ELSE 0 END) AS someday,
                  sum(CASE WHEN done = 1 THEN 1 ELSE 0 END) AS done
           FROM tasks""",
        (today, today, today),
    )
    return {k: (v or 0) for k, v in (row or {}).items()}


def search(query: str, limit: int = 8) -> list[dict]:
    like = f"%{query.strip()}%"
    return [to_api(r) for r in db.q(
        "SELECT * FROM tasks WHERE title LIKE ? OR details LIKE ? ORDER BY done, updated_at DESC LIMIT ?",
        (like, like, limit),
    )]


def clear_done(older_than_days: int = 0) -> int:
    cutoff = now_ms() - older_than_days * 86400 * 1000
    with db.tx() as c:
        c.execute("UPDATE events SET task_id = NULL WHERE task_id IN (SELECT id FROM tasks WHERE done = 1 AND done_at < ?)", (cutoff,))
        return c.execute("DELETE FROM tasks WHERE done = 1 AND done_at < ?", (cutoff,)).rowcount


def due_soon(days: int = 7) -> list[dict]:
    today = date.fromisoformat(today_str())
    return list_tasks("open", due_to=(today + timedelta(days=days)).isoformat())


# ---------------------------------------------------------------------------
# Time blocking: find an open stretch on the calendar and set it aside for a task
# ---------------------------------------------------------------------------
def blocks(task_id: str) -> list[dict]:
    from . import events

    return [events.to_api(r) for r in db.q("SELECT * FROM events WHERE task_id = ? ORDER BY start", (task_id,))]


def free_slots(day: str, minutes: int, limit: int = 6) -> list[dict]:
    """Open stretches of at least `minutes` between the start and end of your working day
    (8:00 to 18:00 inside the calendar's day hours), around timed events. Today starts from now."""
    from . import events

    d = date.fromisoformat(day)
    minutes = max(15, min(int(minutes), 8 * 60))
    cal = db.get_settings()["calendar"]
    lo = datetime.combine(d, datetime.min.time()).replace(hour=max(8, int(cal.get("day_start", 7))))
    hi = datetime.combine(d, datetime.min.time()).replace(hour=min(18, int(cal.get("day_end", 22))))
    now = local_now().replace(tzinfo=None, second=0, microsecond=0)
    if d == now.date():
        lo = max(lo, now + timedelta(minutes=(15 - now.minute % 15) % 15 or 15))
    busy = []
    for o in events.occurrences_between(d, d):
        if o["all_day"]:
            continue
        a, b = datetime.fromisoformat(o["start"]), datetime.fromisoformat(o["end"])
        busy.append((max(a, lo), min(b, hi)))
    busy = sorted((a, b) for a, b in busy if b > a)
    out, cursor = [], lo
    for a, b in [*busy, (hi, hi)]:
        start = cursor
        while start + timedelta(minutes=minutes) <= a and len(out) < limit:
            out.append({"start": start.strftime("%Y-%m-%dT%H:%M"), "end": (start + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M")})
            start += timedelta(minutes=max(minutes, 60))
        cursor = max(cursor, b)
        if len(out) >= limit:
            break
    return out


def block_time(task_id: str, start: str, minutes: int, calendar_id: str | None = None) -> dict:
    from . import events

    t = get(task_id)
    try:
        st = datetime.fromisoformat(start[:16])
    except ValueError as exc:
        raise ValueError("Pick a start time.") from exc
    minutes = max(15, min(int(minutes), 8 * 60))
    return events.create({
        "title": t["title"], "start": st.strftime("%Y-%m-%dT%H:%M"),
        "end": (st + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M"),
        "calendar_id": calendar_id, "description": "Time set aside for this task.",
        "note_id": t["note_id"], "task_id": task_id, "reminders": [5],
    })
