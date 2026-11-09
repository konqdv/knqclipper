"""Persistent job queue.

Jobs are stored in the ``jobs`` table so they survive restarts. Two dispatch backends:

* ``LocalJobQueue``: asyncio workers inside the API process (default, zero dependencies).
* ``RedisJobQueue``: job ids are pushed to Redis lists; independent worker processes
  (``clipper worker render``) pop and execute them against the same database.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select, update

from clipper.core.events import bus
from clipper.db.models import Job
from clipper.db.session import session_scope

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]
_handlers: dict[str, Handler] = {}
MAX_ATTEMPTS = 2


def register_handler(job_type: str, fn: Handler) -> None:
    _handlers[job_type] = fn


async def _run_job(job_id: int) -> None:
    async with session_scope() as s:
        job = await s.get(Job, job_id)
        if job is None or job.status in ("done", "cancelled"):
            return
        job.status = "running"
        job.attempts += 1
        job_type, payload, attempts = job.type, dict(job.payload), job.attempts
    handler = _handlers.get(job_type)
    if handler is None:
        log.error("no handler for job type %s", job_type)
        return
    t0 = time.time()
    try:
        await handler(payload)
    except Exception as exc:  # noqa: BLE001
        log.error("job %s (%s) failed: %s", job_id, job_type, exc)
        status = "queued" if attempts < MAX_ATTEMPTS and not getattr(exc, "permanent", False) else "failed"
        async with session_scope() as s:
            await s.execute(update(Job).where(Job.id == job_id).values(
                status=status, error=f"{exc}\n{traceback.format_exc()[-2000:]}"))
        bus.publish("job.failed" if status == "failed" else "job.retry", job_id=job_id, type=job_type, error=str(exc))
        if status == "queued":
            raise _Retry from exc
        return
    async with session_scope() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(status="done", error=None))
    log.info("job %s (%s) done in %.1fs", job_id, job_type, time.time() - t0)


class _Retry(Exception):
    pass


class PermanentJobError(Exception):
    permanent = True


class JobQueue:
    async def enqueue(self, job_type: str, payload: dict[str, Any]) -> int:
        async with session_scope() as s:
            job = Job(type=job_type, payload=payload, status="queued")
            s.add(job)
            await s.flush()
            job_id = job.id
        await self._dispatch(job_type, job_id)
        bus.publish("job.queued", job_id=job_id, type=job_type)
        return job_id

    async def _dispatch(self, job_type: str, job_id: int) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    async def start(self, job_types: list[str], concurrency: dict[str, int]) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    async def recover(self) -> int:
        """Re-dispatch jobs that were queued or running when the process stopped."""
        async with session_scope() as s:
            rows = (await s.execute(select(Job).where(Job.status.in_(["queued", "running"])))).scalars().all()
            items = [(j.type, j.id) for j in rows]
            for j in rows:
                j.status = "queued"
        for t, i in items:
            await self._dispatch(t, i)
        return len(items)

    def pending(self) -> dict[str, int]:
        return {}


class LocalJobQueue(JobQueue):
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[int]] = {}
        self._workers: list[asyncio.Task] = []

    def _q(self, job_type: str) -> asyncio.Queue[int]:
        return self._queues.setdefault(job_type, asyncio.Queue())

    async def _dispatch(self, job_type: str, job_id: int) -> None:
        self._q(job_type).put_nowait(job_id)

    async def start(self, job_types: list[str], concurrency: dict[str, int]) -> None:
        for t in job_types:
            for _ in range(max(1, concurrency.get(t, 1))):
                self._workers.append(asyncio.create_task(self._worker(t), name=f"jobs-{t}"))

    async def _worker(self, job_type: str) -> None:
        q = self._q(job_type)
        while True:
            job_id = await q.get()
            try:
                await _run_job(job_id)
            except _Retry:
                await asyncio.sleep(3)
                q.put_nowait(job_id)
            except Exception:  # pragma: no cover - defensive
                log.exception("job worker crashed on %s", job_id)
            finally:
                q.task_done()

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    def pending(self) -> dict[str, int]:
        return {k: q.qsize() for k, q in self._queues.items()}


class RedisJobQueue(JobQueue):
    KEY = "clipper:jobs:{}"

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url)
        self._workers: list[asyncio.Task] = []
        self._pending: dict[str, int] = {}

    async def _dispatch(self, job_type: str, job_id: int) -> None:
        await self._redis.lpush(self.KEY.format(job_type), job_id)

    async def start(self, job_types: list[str], concurrency: dict[str, int]) -> None:
        for t in job_types:
            for _ in range(max(1, concurrency.get(t, 1))):
                self._workers.append(asyncio.create_task(self._worker(t), name=f"redis-jobs-{t}"))

    async def _worker(self, job_type: str) -> None:
        key = self.KEY.format(job_type)
        while True:
            item = await self._redis.brpop([key], timeout=5)
            if not item:
                continue
            job_id = int(item[1])
            try:
                await _run_job(job_id)
            except _Retry:
                await asyncio.sleep(3)
                await self._redis.lpush(key, job_id)
            except Exception:  # pragma: no cover
                log.exception("redis job worker crashed on %s", job_id)

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        await self._redis.aclose()


_queue: JobQueue | None = None


def get_queue() -> JobQueue:
    if _queue is None:
        raise RuntimeError("job queue not initialised")
    return _queue


def set_queue(q: JobQueue) -> None:
    global _queue
    _queue = q
