"""Private calendar feed: an .ics address your phone's own Calendar app can subscribe to.

The address carries a long random secret and is only answered on this Mac, your
tailnet (Tailscale) or your home network, never the public internet. Add it on the
phone itself (Settings → Apps → Calendar → Calendar Accounts → Add Subscribed Calendar) so the
phone fetches it directly; a calendar subscribed through iCloud would be fetched
by Apple's servers instead, which can't (and shouldn't) reach Daybook.
"""
from __future__ import annotations

import hmac
import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response

from .. import db, security
from ..bus import changed
from ..services import events
from ..util import jloads, tzinfo

router = APIRouter()
DETAILS = ("full", "titles", "busy")


def _feed_settings() -> dict:
    return db.get_settings()["feed"]


def _public(f: dict) -> dict:
    return {"enabled": bool(f.get("enabled")), "detail": f.get("detail", "full"), "include_tasks": bool(f.get("include_tasks", True)),
            "path": f"/feed/{f['token']}.ics" if f.get("enabled") and f.get("token") else None}


@router.get("/api/feed")
def feed_info():
    return _public(_feed_settings())


@router.post("/api/feed")
def feed_update(data: dict = Body(...)):
    f = dict(_feed_settings())
    if "enabled" in data:
        f["enabled"] = bool(data["enabled"])
    if data.get("detail") in DETAILS:
        f["detail"] = data["detail"]
    if "include_tasks" in data:
        f["include_tasks"] = bool(data["include_tasks"])
    if f["enabled"] and not f.get("token"):
        f["token"] = secrets.token_urlsafe(32)
    db.update_settings({"feed": f})
    changed("settings")
    return _public(f)


@router.post("/api/feed/reset")
def feed_reset():
    f = dict(_feed_settings())
    f["token"] = secrets.token_urlsafe(32)
    db.update_settings({"feed": f})
    changed("settings")
    return _public(f)


def build_feed(detail: str, include_tasks: bool, days_back: int = 30, days_ahead: int = 365) -> bytes:
    from icalendar import Alarm, Calendar, Event, vRecur

    tz = tzinfo()
    cal = Calendar()
    cal.add("prodid", "-//Daybook//Private calendar feed//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Daybook")
    cal.add("x-wr-timezone", str(tz.key))
    cal.add("refresh-interval", timedelta(minutes=15), parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", "PT15M")
    now = datetime.now(tz=tz)
    lo = (now - timedelta(days=days_back)).date().isoformat()
    hi = (now + timedelta(days=days_ahead)).date().isoformat()
    stamp = datetime.now(tz=tzinfo("UTC"))
    for ev in db.q("SELECT * FROM events WHERE (rrule IS NOT NULL AND rrule != '') OR (substr(end, 1, 10) >= ? AND substr(start, 1, 10) <= ?) "
                   "ORDER BY start", (lo, hi)):
        e = Event()
        e.add("uid", ev["uid"] or f"{ev['id']}@daybook")
        e.add("summary", "Busy" if detail == "busy" else ev["title"])
        if detail == "full":
            if ev["description"]:
                e.add("description", ev["description"])
            if ev["location"]:
                e.add("location", ev["location"])
        all_day = bool(ev["all_day"])
        start = events.parse_dt(ev["start"], all_day)
        end = events.parse_dt(ev["end"], all_day)
        if all_day:
            e.add("dtstart", start.date())
            e.add("dtend", (end + timedelta(days=1)).date())
        else:
            e.add("dtstart", start.replace(tzinfo=tz))
            e.add("dtend", end.replace(tzinfo=tz))
        if ev["rrule"]:
            e.add("rrule", vRecur.from_ical(ev["rrule"]))
            for ex in jloads(ev["exdates"], []):
                exd = events.parse_dt(ex, all_day)
                e.add("exdate", exd.date() if all_day else exd.replace(tzinfo=tz))
        if detail == "full":
            for minutes in jloads(ev["reminders"], []):
                a = Alarm()
                a.add("action", "DISPLAY")
                a.add("description", ev["title"])
                a.add("trigger", timedelta(minutes=-int(minutes)))
                e.add_component(a)
        if detail == "busy":
            e.add("transp", "OPAQUE")
            e.add("class", "PRIVATE")
        e.add("dtstamp", stamp)
        cal.add_component(e)
    if include_tasks and detail != "busy":
        for t in db.q("SELECT * FROM tasks WHERE done = 0 AND due_date IS NOT NULL AND due_date >= ? AND due_date <= ?", (lo, hi)):
            e = Event()
            e.add("uid", f"task-{t['id']}@daybook")
            e.add("summary", f"☐ {t['title']}")
            if detail == "full" and t["details"]:
                e.add("description", t["details"])
            d = date.fromisoformat(t["due_date"])
            if t["due_time"]:
                hh, mm = (int(x) for x in t["due_time"].split(":")[:2])
                s = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz)
                e.add("dtstart", s)
                e.add("dtend", s + timedelta(minutes=15))
            else:
                e.add("dtstart", d)
                e.add("dtend", d + timedelta(days=1))
            e.add("transp", "TRANSPARENT")
            e.add("dtstamp", stamp)
            cal.add_component(e)
    return cal.to_ical()


@router.get("/feed/{token}.ics")
def feed(token: str, request: Request):
    f = _feed_settings()
    ip = request.scope.get("state", {}).get("client_ip") or (request.client.host if request.client else "")
    if not f.get("enabled") or not f.get("token") or not hmac.compare_digest(token, f["token"]):
        raise HTTPException(404, "Not found.")
    if not security.is_local_address(ip):
        raise HTTPException(403, "This feed is only available on your own devices.")
    body = build_feed(f.get("detail", "full"), bool(f.get("include_tasks", True)),
                      int(f.get("days_back", 30)), int(f.get("days_ahead", 365)))
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="daybook.ics"', "Cache-Control": "no-store"})
