"""Calendar: calendars, events, recurrence expansion and .ics import/export.

Times are stored as floating local time in the user's timezone setting:
all-day events as 'YYYY-MM-DD' (end date inclusive), timed events as
'YYYY-MM-DDTHH:MM'. Recurrence uses RFC 5545 RRULE strings.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta

from dateutil.rrule import rrulestr

from .. import db
from ..bus import changed
from ..util import jloads, new_id, now_ms, tzinfo

DEFAULT_CALENDARS = [
    ("Work", "#2B5F97"),
    ("Personal", "#1F7A72"),
    ("Urgent", "#C23A2E"),
    ("Routine", "#6A4C9C"),
    ("Social", "#B07A1A"),
]


class NotFound(Exception):
    pass


# ---------------------------------------------------------------------------
# Calendars
# ---------------------------------------------------------------------------
def seed_calendars() -> None:
    if db.scalar("SELECT count(*) FROM calendars"):
        return
    with db.tx() as c:
        for i, (name, color) in enumerate(DEFAULT_CALENDARS):
            c.execute(
                "INSERT INTO calendars(id, name, color, visible, sort, created_at) VALUES (?,?,?,?,?,?)",
                (new_id(), name, color, 1, i, now_ms()),
            )


def calendars() -> list[dict]:
    rows = db.q("SELECT * FROM calendars ORDER BY sort, name")
    for r in rows:
        r["visible"] = bool(r["visible"])
    return rows


def calendar_by_name(name: str) -> dict | None:
    return db.q1("SELECT * FROM calendars WHERE lower(name) = lower(?)", (name.strip(),))


def save_calendar(data: dict, cal_id: str | None = None) -> dict:
    name = (data.get("name") or "").strip()[:60]
    color = data.get("color") or "#2B5F97"
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise ValueError("Color must look like #2B5F97.")
    with db.tx() as c:
        if cal_id:
            row = c.execute("SELECT * FROM calendars WHERE id = ?", (cal_id,)).fetchone()
            if not row:
                raise NotFound(cal_id)
            c.execute(
                "UPDATE calendars SET name = ?, color = ?, visible = ? WHERE id = ?",
                (name or row["name"], color, 1 if data.get("visible", True) else 0, cal_id),
            )
        else:
            if not name:
                raise ValueError("Give the calendar a name.")
            cal_id = new_id()
            sort = (db.scalar("SELECT max(sort) FROM calendars") or 0) + 1
            c.execute(
                "INSERT INTO calendars(id, name, color, visible, sort, created_at) VALUES (?,?,?,?,?,?)",
                (cal_id, name, color, 1, sort, now_ms()),
            )
    changed("calendars", cal_id)
    return db.q1("SELECT * FROM calendars WHERE id = ?", (cal_id,))


def delete_calendar(cal_id: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE events SET calendar_id = NULL WHERE calendar_id = ?", (cal_id,))
        c.execute("DELETE FROM calendars WHERE id = ?", (cal_id,))
    changed("calendars", cal_id)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def parse_dt(value: str, all_day: bool) -> datetime:
    if all_day:
        return datetime.combine(date.fromisoformat(value[:10]), time(0, 0))
    v = value.replace(" ", "T")
    if len(v) == 10:
        return datetime.combine(date.fromisoformat(v), time(9, 0))
    return datetime.fromisoformat(v[:16])


def fmt_dt(dt: datetime, all_day: bool) -> str:
    return dt.strftime("%Y-%m-%d") if all_day else dt.strftime("%Y-%m-%dT%H:%M")


def _clean_rrule(rule: str | None) -> str | None:
    if not rule:
        return None
    rule = rule.strip()
    if rule.upper().startswith("RRULE:"):
        rule = rule[6:]
    # Floating local time: drop UTC markers on UNTIL so dateutil accepts it.
    rule = re.sub(r"(UNTIL=\d{8}(T\d{6})?)Z", r"\1", rule, flags=re.I)
    if not re.fullmatch(r"[A-Z0-9=;,:+\-]+", rule.upper()):
        raise ValueError("Repeat rule isn't valid.")
    try:
        rrulestr(rule, dtstart=datetime(2020, 1, 1))
    except (ValueError, TypeError) as exc:
        raise ValueError("Repeat rule isn't valid.") from exc
    return rule


def _validate(data: dict) -> dict:
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("Add a title.")
    all_day = bool(data.get("all_day"))
    try:
        start = parse_dt(data["start"], all_day)
        end = parse_dt(data.get("end") or data["start"], all_day)
    except (KeyError, ValueError) as exc:
        raise ValueError("Start and end need valid dates.") from exc
    if all_day:
        end = max(end, start)
    elif end <= start:
        end = start + timedelta(minutes=int(db.get_settings()["calendar"]["default_duration"]))
    reminders = sorted({int(x) for x in (data.get("reminders") or []) if str(x).lstrip("-").isdigit() and 0 <= int(x) <= 40320})
    cal = data.get("calendar_id")
    if cal and not db.q1("SELECT id FROM calendars WHERE id = ?", (cal,)):
        cal = None
    note_id = data.get("note_id") or None
    if note_id and not db.q1("SELECT id FROM notes WHERE id = ?", (note_id,)):
        note_id = None
    task_id = data.get("task_id") or None
    if task_id and not db.q1("SELECT id FROM tasks WHERE id = ?", (task_id,)):
        task_id = None
    return {
        "title": title[:300],
        "description": (data.get("description") or "")[:20000],
        "location": (data.get("location") or "")[:500],
        "calendar_id": cal,
        "all_day": 1 if all_day else 0,
        "start": fmt_dt(start, all_day),
        "end": fmt_dt(end, all_day),
        "rrule": _clean_rrule(data.get("rrule")),
        "reminders": json.dumps(reminders),
        "note_id": note_id,
        "task_id": task_id,
    }


def to_api(row: dict) -> dict:
    out = dict(row)
    out["all_day"] = bool(row["all_day"])
    out["reminders"] = jloads(row["reminders"], [])
    out["exdates"] = jloads(row["exdates"], [])
    return out


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def get(event_id: str) -> dict:
    row = db.q1("SELECT * FROM events WHERE id = ?", (event_id,))
    if not row:
        raise NotFound(event_id)
    return to_api(row)


def create(data: dict, *, uid: str | None = None, emit: bool = True) -> dict:
    v = _validate(data)
    eid = new_id()
    ts = now_ms()
    with db.tx() as c:
        c.execute(
            """INSERT INTO events(id, title, description, location, calendar_id, all_day, start, end, rrule, exdates,
                   reminders, note_id, task_id, uid, created_at, updated_at)
               VALUES (:id, :title, :description, :location, :calendar_id, :all_day, :start, :end, :rrule, :exdates,
                   :reminders, :note_id, :task_id, :uid, :ts, :ts)""",
            {**v, "id": eid, "exdates": json.dumps(data.get("exdates") or []), "uid": uid or f"{eid}@daybook", "ts": ts},
        )
    if emit:
        changed("events", eid)
    return get(eid)


def update(event_id: str, data: dict, *, scope: str = "all", occurrence: str | None = None) -> dict:
    current = get(event_id)
    merged = {**current, **data}
    if scope == "this" and current["rrule"] and occurrence:
        # Detach this one occurrence into its own event.
        _add_exdate(event_id, occurrence)
        merged.pop("rrule", None)
        single = {k: merged.get(k) for k in ("title", "description", "location", "calendar_id", "all_day", "start", "end", "reminders", "note_id")}
        single["rrule"] = None
        return create(single)
    if scope == "following" and current["rrule"] and occurrence:
        occ_dt = parse_dt(occurrence, current["all_day"])
        until = occ_dt - timedelta(seconds=1)
        rule = re.sub(r";?(UNTIL|COUNT)=[^;]*", "", current["rrule"], flags=re.I).strip(";")
        rule = f"{rule};UNTIL={until.strftime('%Y%m%dT%H%M%S')}"
        with db.tx() as c:
            c.execute("UPDATE events SET rrule = ?, updated_at = ? WHERE id = ?", (rule, now_ms(), event_id))
        follow = {k: merged.get(k) for k in ("title", "description", "location", "calendar_id", "all_day", "start", "end", "reminders", "note_id", "rrule")}
        return create(follow)
    v = _validate(merged)
    with db.tx() as c:
        c.execute(
            """UPDATE events SET title=:title, description=:description, location=:location, calendar_id=:calendar_id,
                   all_day=:all_day, start=:start, end=:end, rrule=:rrule, reminders=:reminders, note_id=:note_id,
                   updated_at=:ts WHERE id=:id""",
            {**v, "id": event_id, "ts": now_ms()},
        )
    changed("events", event_id)
    return get(event_id)


def _add_exdate(event_id: str, occurrence: str) -> None:
    ev = get(event_id)
    ex = list(dict.fromkeys([*ev["exdates"], occurrence]))
    with db.tx() as c:
        c.execute("UPDATE events SET exdates = ?, updated_at = ? WHERE id = ?", (json.dumps(ex), now_ms(), event_id))


def delete(event_id: str, *, scope: str = "all", occurrence: str | None = None) -> None:
    ev = get(event_id)
    if ev["rrule"] and occurrence and scope == "this":
        _add_exdate(event_id, occurrence)
    elif ev["rrule"] and occurrence and scope == "following":
        occ_dt = parse_dt(occurrence, ev["all_day"])
        until = occ_dt - timedelta(seconds=1)
        rule = re.sub(r";?(UNTIL|COUNT)=[^;]*", "", ev["rrule"], flags=re.I).strip(";")
        with db.tx() as c:
            c.execute(
                "UPDATE events SET rrule = ?, updated_at = ? WHERE id = ?",
                (f"{rule};UNTIL={until.strftime('%Y%m%dT%H%M%S')}", now_ms(), event_id),
            )
    else:
        with db.tx() as c:
            c.execute("DELETE FROM events WHERE id = ?", (event_id,))
    changed("events", event_id)


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------
def _occurrence(ev: dict, start: datetime, dur: timedelta, recurring: bool, cal: dict | None) -> dict:
    all_day = bool(ev["all_day"])
    end = start + dur
    key = fmt_dt(start, all_day)
    return {
        "id": f"{ev['id']}::{key}" if recurring else ev["id"],
        "event_id": ev["id"],
        "occurrence": key if recurring else None,
        "title": ev["title"],
        "description": ev["description"],
        "location": ev["location"],
        "calendar_id": ev["calendar_id"],
        "color": (cal or {}).get("color") or "#56665C",
        "calendar": (cal or {}).get("name"),
        "all_day": all_day,
        "start": key,
        "end": fmt_dt(end, all_day),
        "recurring": recurring,
        "rrule": ev["rrule"],
        "reminders": jloads(ev["reminders"], []),
        "note_id": ev["note_id"],
        "task_id": ev.get("task_id"),
    }


def expand(ev: dict, range_start: datetime, range_end: datetime, cals: dict | None = None) -> list[dict]:
    """Occurrences of `ev` overlapping [range_start, range_end)."""
    all_day = bool(ev["all_day"])
    start = parse_dt(ev["start"], all_day)
    end = parse_dt(ev["end"], all_day)
    if all_day:
        end = end + timedelta(days=1)  # inclusive end date -> exclusive instant
    dur = max(end - start, timedelta(minutes=1) if not all_day else timedelta(days=1))
    cal = (cals or {}).get(ev["calendar_id"])
    out: list[dict] = []

    def emit(s: datetime) -> None:
        occ = _occurrence(ev, s, dur - (timedelta(days=1) if all_day else timedelta(0)), bool(ev["rrule"]), cal)
        out.append(occ)

    if not ev["rrule"]:
        if start < range_end and start + dur > range_start:
            emit(start)
        return out
    try:
        rule = rrulestr(ev["rrule"], dtstart=start)
    except (ValueError, TypeError):
        return out
    excluded = set(jloads(ev["exdates"], []))
    for s in rule.between(range_start - dur, range_end, inc=True):
        if s + dur <= range_start or s >= range_end:
            continue
        if fmt_dt(s, all_day) in excluded:
            continue
        emit(s)
        if len(out) > 2000:
            break
    return out


def occurrences_between(first: date, last: date, *, include_hidden: bool = False) -> list[dict]:
    rs = datetime.combine(first, time(0, 0))
    re_ = datetime.combine(last + timedelta(days=1), time(0, 0))
    cals = {c["id"]: c for c in calendars()}
    hidden = {cid for cid, c in cals.items() if not c["visible"]}
    rows = db.q(
        "SELECT * FROM events WHERE rrule IS NOT NULL OR (start < ? AND end >= ?)",
        (fmt_dt(re_, False), first.isoformat()),
    )
    out: list[dict] = []
    for ev in rows:
        if not include_hidden and ev["calendar_id"] in hidden:
            continue
        out.extend(expand(ev, rs, re_, cals))
    out.sort(key=lambda o: (o["start"][:10], 0 if o["all_day"] else 1, o["start"], o["title"].lower()))
    return out


def upcoming(days: int = 14, limit: int = 50) -> list[dict]:
    from ..util import local_now

    now = local_now().replace(tzinfo=None)
    items = occurrences_between(now.date(), now.date() + timedelta(days=days))
    items = [o for o in items if o["all_day"] and o["end"] >= now.strftime("%Y-%m-%d") or (not o["all_day"] and o["end"] >= now.strftime("%Y-%m-%dT%H:%M"))]
    return items[:limit]


def search(query: str, limit: int = 8) -> list[dict]:
    like = f"%{query.strip()}%"
    return [to_api(r) for r in db.q(
        "SELECT * FROM events WHERE title LIKE ? OR description LIKE ? OR location LIKE ? ORDER BY start DESC LIMIT ?",
        (like, like, like, limit),
    )]


# ---------------------------------------------------------------------------
# iCalendar
# ---------------------------------------------------------------------------
def _to_local_naive(value, tz) -> tuple[datetime, bool]:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(tz).replace(tzinfo=None)
        return value, False
    if isinstance(value, date):
        return datetime.combine(value, time(0, 0)), True
    raise ValueError("unsupported date value")


def import_ics(data: bytes, calendar_id: str | None = None) -> dict:
    from icalendar import Calendar

    tz = tzinfo()
    try:
        cal = Calendar.from_ical(data)
    except Exception as exc:
        raise ValueError("That file isn't a readable .ics calendar.") from exc
    created = updated = skipped = 0
    for comp in cal.walk("VEVENT"):
        try:
            title = str(comp.get("SUMMARY") or "Untitled event")
            dtstart = comp.get("DTSTART")
            if dtstart is None:
                skipped += 1
                continue
            start, all_day = _to_local_naive(dtstart.dt, tz)
            if comp.get("DTEND") is not None:
                end, _ = _to_local_naive(comp.get("DTEND").dt, tz)
                if all_day:
                    end = end - timedelta(days=1)  # ICS all-day DTEND is exclusive
            elif comp.get("DURATION") is not None:
                end = start + comp.get("DURATION").dt
            else:
                end = start if all_day else start + timedelta(hours=1)
            rrule = None
            if comp.get("RRULE") is not None:
                rrule = comp.get("RRULE").to_ical().decode()
                m = re.search(r"UNTIL=(\d{8}T\d{6})Z", rrule)
                if m:
                    until_utc = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=tzinfo("UTC"))
                    rrule = rrule.replace(m.group(0), "UNTIL=" + until_utc.astimezone(tz).strftime("%Y%m%dT%H%M%S"))
            exdates: list[str] = []
            ex = comp.get("EXDATE")
            for exprop in (ex if isinstance(ex, list) else [ex] if ex is not None else []):
                for d in exprop.dts:
                    exd, _ = _to_local_naive(d.dt, tz)
                    exdates.append(fmt_dt(exd, all_day))
            reminders = []
            for alarm in comp.walk("VALARM"):
                trig = alarm.get("TRIGGER")
                if trig is not None and isinstance(trig.dt, timedelta) and trig.dt <= timedelta(0):
                    reminders.append(int(-trig.dt.total_seconds() // 60))
            uid = str(comp.get("UID") or "") or None
            payload = {
                "title": title,
                "description": str(comp.get("DESCRIPTION") or ""),
                "location": str(comp.get("LOCATION") or ""),
                "calendar_id": calendar_id,
                "all_day": all_day,
                "start": fmt_dt(start, all_day),
                "end": fmt_dt(end, all_day),
                "rrule": rrule,
                "reminders": reminders,
                "exdates": exdates,
            }
            existing = db.q1("SELECT id FROM events WHERE uid = ?", (uid,)) if uid else None
            if existing:
                update(existing["id"], payload)
                if exdates:
                    with db.tx() as c:
                        c.execute("UPDATE events SET exdates = ? WHERE id = ?", (json.dumps(exdates), existing["id"]))
                updated += 1
            else:
                create(payload, uid=uid, emit=False)
                created += 1
        except Exception:
            skipped += 1
    changed("events")
    return {"created": created, "updated": updated, "skipped": skipped}


def export_ics() -> bytes:
    from icalendar import Alarm, Calendar, Event, vRecur

    tz = tzinfo()
    cal = Calendar()
    cal.add("prodid", "-//Daybook//Private calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-timezone", str(tz.key))
    for ev in db.q("SELECT * FROM events ORDER BY start"):
        e = Event()
        e.add("uid", ev["uid"] or f"{ev['id']}@daybook")
        e.add("summary", ev["title"])
        if ev["description"]:
            e.add("description", ev["description"])
        if ev["location"]:
            e.add("location", ev["location"])
        all_day = bool(ev["all_day"])
        start = parse_dt(ev["start"], all_day)
        end = parse_dt(ev["end"], all_day)
        if all_day:
            e.add("dtstart", start.date())
            e.add("dtend", (end + timedelta(days=1)).date())
        else:
            e.add("dtstart", start.replace(tzinfo=tz))
            e.add("dtend", end.replace(tzinfo=tz))
        if ev["rrule"]:
            e.add("rrule", vRecur.from_ical(ev["rrule"]))
            for ex in jloads(ev["exdates"], []):
                exd = parse_dt(ex, all_day)
                e.add("exdate", exd.date() if all_day else exd.replace(tzinfo=tz))
        for minutes in jloads(ev["reminders"], []):
            a = Alarm()
            a.add("action", "DISPLAY")
            a.add("description", ev["title"])
            a.add("trigger", timedelta(minutes=-int(minutes)))
            e.add_component(a)
        e.add("dtstamp", datetime.now(tz=tzinfo("UTC")))
        cal.add_component(e)
    return cal.to_ical()
