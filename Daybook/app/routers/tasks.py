"""Tasks."""
from __future__ import annotations

from fastapi import APIRouter, Body

from ..services import tasks
from ..util import today_str

router = APIRouter(prefix="/api/tasks")


@router.get("")
def list_tasks(view: str = "open", list: str | None = None, due_from: str | None = None, due_to: str | None = None):  # noqa: A002
    return {"items": tasks.list_tasks(view, list, due_from, due_to), "counts": tasks.counts(), "lists": tasks.lists()}


@router.post("")
def create_task(payload: dict = Body(...)):
    return tasks.create(payload)


@router.get("/{task_id}")
def get_task(task_id: str):
    return tasks.get(task_id)


@router.put("/{task_id}")
def update_task(task_id: str, payload: dict = Body(...)):
    return tasks.update(task_id, payload)


@router.delete("/{task_id}")
def delete_task(task_id: str):
    tasks.delete(task_id)
    return {"ok": True}


@router.get("/{task_id}/time")
def task_time(task_id: str, day: str | None = None, minutes: int = 60):
    t = tasks.get(task_id)
    day = day or (t["due_date"] if t["due_date"] and t["due_date"] >= today_str() else today_str())
    return {"day": day, "minutes": minutes, "blocks": tasks.blocks(task_id), "slots": tasks.free_slots(day, minutes)}


@router.post("/{task_id}/time")
def block_task_time(task_id: str, payload: dict = Body(...)):
    return tasks.block_time(task_id, str(payload.get("start") or ""), int(payload.get("minutes") or 60), payload.get("calendar_id"))


@router.post("/clear-done")
def clear_done():
    return {"deleted": tasks.clear_done()}
