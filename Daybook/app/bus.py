"""In-process event bus feeding the live update stream (Server-Sent Events).

Worker threads publish; each connected browser tab has a queue. This keeps a
phone and a laptop in sync without polling, and carries reminders and import
progress.
"""
from __future__ import annotations

import asyncio
from typing import Any


class Bus:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.subscribers: set[asyncio.Queue] = set()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            return

        def _deliver() -> None:
            for q in list(self.subscribers):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _deliver()
        else:
            try:
                loop.call_soon_threadsafe(_deliver)
            except RuntimeError:
                pass


bus = Bus()


def changed(entity: str, id: str | None = None, **extra: Any) -> None:
    bus.publish({"type": "changed", "entity": entity, "id": id, **extra})
