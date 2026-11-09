"""Calendars and events (with recurrence) plus .ics import/export."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Body, File, Form, UploadFile
from fastapi.responses import Response

from ..services import events

router = APIRouter(prefix="/api")


@router.get("/calendars")
def list_calendars():
    return events.calendars()


@router.post("/calendars")
def create_calendar(payload: dict = Body(...)):
    return events.save_calendar(payload)


@router.put("/calendars/{cal_id}")
def update_calendar(cal_id: str, payload: dict = Body(...)):
    return events.save_calendar(payload, cal_id)


@router.delete("/calendars/{cal_id}")
def delete_calendar(cal_id: str):
    events.delete_calendar(cal_id)
    return {"ok": True}


@router.get("/events")
def list_events(start: str, end: str):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if (last - first) > timedelta(days=400):
        raise ValueError("Ask for at most about a year of events at a time.")
    return events.occurrences_between(first, last)


@router.get("/events/upcoming")
def upcoming(days: int = 14):
    return events.upcoming(days=min(days, 90))


@router.get("/events/{event_id}/prep")
def event_prep(event_id: str, start: str | None = None):
    from ..services import prep

    return prep.build(event_id, start)


@router.get("/events/{event_id}")
def get_event(event_id: str):
    return events.get(event_id)


@router.post("/events")
def create_event(payload: dict = Body(...)):
    return events.create(payload)


@router.put("/events/{event_id}")
def update_event(event_id: str, payload: dict = Body(...), scope: str = "all", occurrence: str | None = None):
    return events.update(event_id, payload, scope=scope, occurrence=occurrence)


@router.delete("/events/{event_id}")
def delete_event(event_id: str, scope: str = "all", occurrence: str | None = None):
    events.delete(event_id, scope=scope, occurrence=occurrence)
    return {"ok": True}


@router.post("/events/import")
async def import_ics(file: UploadFile = File(...), calendar_id: str | None = Form(None)):
    data = await file.read()
    return events.import_ics(data, calendar_id=calendar_id or None)


@router.get("/calendar.ics")
def export_ics():
    return Response(events.export_ics(), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=daybook-calendar.ics"})
