"""Restart Daybook into the code that's on disk, after checking that it can start."""
from __future__ import annotations

from anyio import to_thread
from fastapi import APIRouter, HTTPException

from ..services import updates

router = APIRouter(prefix="/api")


@router.get("/system/update")
def update_status():
    return updates.status()


@router.post("/system/restart")
async def restart():
    # The start-up check takes a few seconds (longer if requirements changed); don't hold up the server.
    try:
        return await to_thread.run_sync(updates.restart)
    except updates.UpdateError as exc:
        raise HTTPException(409, str(exc)) from None
