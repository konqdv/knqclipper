"""In-process publish/subscribe bus used to push live updates to WebSocket clients.

When ``queue_backend=redis`` the bus also relays events through a Redis pub/sub channel so
that external render workers can notify the API process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

REDIS_CHANNEL = "clipper:events"


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "data": self.data, "ts": self.ts}, default=str)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._redis = None
        self._relay_task: asyncio.Task | None = None
        self.origin = uuid.uuid4().hex

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, maxsize: int = 500) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(q)

    def _deliver(self, ev: Event) -> None:
        for q in list(self._subscribers):
            if q.full():
                try:  # drop the oldest event for slow consumers instead of blocking producers
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(ev)

    def publish(self, type_: str, **data: Any) -> None:
        """Thread-safe publish. Safe to call from worker threads."""
        ev = Event(type_, data)
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is not None and running is not loop:
            loop.call_soon_threadsafe(self._deliver, ev)
        else:
            self._deliver(ev)
        target = loop or running
        if self._redis is not None and target is not None:
            payload = json.dumps({"type": ev.type, "data": ev.data, "ts": ev.ts, "origin": self.origin}, default=str)
            asyncio.run_coroutine_threadsafe(self._redis.publish(REDIS_CHANNEL, payload), target)

    # --- redis relay (optional) ---------------------------------------------------
    async def attach_redis(self, url: str, listen: bool = True) -> None:
        import redis.asyncio as aioredis  # optional dependency

        self._redis = aioredis.from_url(url)
        if listen:
            self._relay_task = asyncio.create_task(self._relay_loop(url))

    async def _relay_loop(self, url: str) -> None:
        import redis.asyncio as aioredis

        client = aioredis.from_url(url)
        pubsub = client.pubsub()
        await pubsub.subscribe(REDIS_CHANNEL)
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                payload = json.loads(msg["data"])
                if payload.get("origin") == self.origin:
                    continue
                self._deliver(Event(payload["type"], payload.get("data", {}), payload.get("ts", time.time())))
            except Exception:  # pragma: no cover - defensive
                log.exception("bad event from redis")

    async def close(self) -> None:
        if self._relay_task:
            self._relay_task.cancel()
        if self._redis is not None:
            await self._redis.aclose()


bus = EventBus()
