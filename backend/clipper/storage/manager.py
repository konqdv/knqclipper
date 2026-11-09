"""Storage Manager: on-disk layout, quotas and cleanup of temporary data.

Layout under ``data_dir``::

    buffer/<session_id>/         rolling-buffer segments (temporary, deleted on eviction / session end)
    sources/<channel_id>/        "clip original": stream-copied source of each detected moment
    clips/                       rendered vertical clips
    thumbs/                      thumbnails
    training/                    imported training examples
    uploads/                     files uploaded to be used as local sources
    work/                        scratch files for renders
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from sqlalchemy import select

from clipper.core.events import bus
from clipper.db.models import Moment, StreamSession
from clipper.db.session import session_scope

log = logging.getLogger(__name__)


class StorageManager:
    def __init__(self, data_dir: Path, min_free_gb: float = 2.0, source_retention_days: int = 14) -> None:
        self.root = data_dir
        self.min_free_gb = min_free_gb
        self.source_retention_days = source_retention_days
        for d in ("buffer", "sources", "clips", "thumbs", "training", "uploads", "work"):
            (self.root / d).mkdir(parents=True, exist_ok=True)

    # --- paths ----------------------------------------------------------------
    def buffer_dir(self, session_id: int) -> Path:
        p = self.root / "buffer" / str(session_id)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def source_path(self, channel_id: int, moment_key: str) -> Path:
        p = self.root / "sources" / str(channel_id)
        p.mkdir(parents=True, exist_ok=True)
        return p / f"moment_{moment_key}.mp4"

    def clip_path(self, clip_id: int, render_no: int) -> Path:
        return self.root / "clips" / f"clip_{clip_id}_r{render_no}.mp4"

    def thumb_path(self, name: str) -> Path:
        return self.root / "thumbs" / f"{name}.jpg"

    def training_path(self, name: str) -> Path:
        return self.root / "training" / name

    def upload_path(self, name: str) -> Path:
        return self.root / "uploads" / name

    def work_dir(self, name: str) -> Path:
        p = self.root / "work" / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def to_media_url(self, path: str | Path | None) -> str | None:
        """Map an absolute file under data_dir to the /media URL served by the API."""
        if not path:
            return None
        try:
            rel = Path(path).resolve().relative_to(self.root)
        except ValueError:
            return None
        return f"/media/{rel.as_posix()}"

    # --- quotas ---------------------------------------------------------------
    def free_gb(self) -> float:
        return shutil.disk_usage(self.root).free / 2**30

    def has_space(self, needed_gb: float = 0.0) -> bool:
        return self.free_gb() - needed_gb >= self.min_free_gb

    # --- cleanup --------------------------------------------------------------
    async def cleanup_once(self) -> dict[str, int]:
        removed_buffers = 0
        removed_sources = 0
        buffer_root = self.root / "buffer"
        async with session_scope() as s:
            active = {
                sid for (sid,) in (await s.execute(
                    select(StreamSession.id).where(StreamSession.status.in_(["starting", "live"]))
                )).all()
            }
        for d in buffer_root.iterdir() if buffer_root.exists() else []:
            if d.is_dir() and d.name.isdigit() and int(d.name) not in active:
                shutil.rmtree(d, ignore_errors=True)
                removed_buffers += 1
        cutoff = time.time() - self.source_retention_days * 86400
        async with session_scope() as s:
            rows = (await s.execute(
                select(Moment).where(Moment.status.in_(["rejected", "discarded"]), Moment.created_at < cutoff,
                                     Moment.source_path.is_not(None))
            )).scalars().all()
            for m in rows:
                if m.source_path:
                    Path(m.source_path).unlink(missing_ok=True)
                m.source_path = None
                removed_sources += 1
        for f in (self.root / "work").glob("*"):
            if f.stat().st_mtime < time.time() - 6 * 3600:
                shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink(missing_ok=True)
        if not self.has_space():
            bus.publish("system.warning", message=f"Low disk space: {self.free_gb():.1f} GB free")
        return {"buffers": removed_buffers, "sources": removed_sources}

    async def run_periodic(self, interval_s: float = 600) -> None:
        while True:
            try:
                res = await self.cleanup_once()
                if any(res.values()):
                    log.info("storage cleanup: %s", res)
            except Exception:  # pragma: no cover - defensive
                log.exception("storage cleanup failed")
            await asyncio.sleep(interval_s)


_storage: StorageManager | None = None


def get_storage() -> StorageManager:
    if _storage is None:
        raise RuntimeError("storage not initialised")
    return _storage


def set_storage(s: StorageManager) -> None:
    global _storage
    _storage = s
