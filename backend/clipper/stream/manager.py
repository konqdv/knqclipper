"""StreamManager: supervises monitored channels (live detection, sessions, reconnection, multistream)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select

from clipper.asr.engine import WhisperEngine
from clipper.connectors.base import ConnectorContext, LiveStatus, ParsedSource
from clipper.connectors.registry import make_connector
from clipper.core.appsettings import channel_config
from clipper.core.events import bus
from clipper.db.models import Channel
from clipper.db.session import session_scope
from clipper.learning.service import learner as default_learner
from clipper.llm.providers import make_provider
from clipper.stream.session import StreamSession

log = logging.getLogger(__name__)
ONE_SHOT = {"local", "vod"}  # sources that finish instead of going offline


def source_from_row(ch: Channel) -> ParsedSource:
    kind = {"local": "file", "vod": "vod", "ingest": "ingest"}.get(ch.platform, "channel")
    if ch.platform == "youtube" and ch.config.get("_kind") == "video":
        kind = "video"
    return ParsedSource(ch.platform, ch.handle, kind, external_id=ch.external_id, display_name=ch.display_name, url=ch.url)


class StreamManager:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.learner = default_learner
        self.llm = make_provider(settings)
        self._asr: WhisperEngine | None = None
        self._asr_failed: str | None = None
        self.supervisors: dict[int, asyncio.Task] = {}
        self.sessions: dict[int, StreamSession] = {}
        self.live_state: dict[int, dict[str, Any]] = {}

    def asr_engine(self) -> WhisperEngine | None:
        if self._asr is None and self._asr_failed is None:
            try:
                s = self.settings
                self._asr = WhisperEngine(s.whisper_model, s.whisper_device, s.whisper_compute_type, s.resolved_models_dir,
                                          workers=s.asr_workers)
            except Exception as exc:  # noqa: BLE001
                self._asr_failed = str(exc)
                log.error("ASR unavailable: %s", exc)
        return self._asr

    def credentials_ctx(self) -> ConnectorContext:
        return ConnectorContext(settings=self.settings)

    # --- control ---------------------------------------------------------------------------
    async def resume(self) -> None:
        async with session_scope() as s:
            ids = [c.id for c in (await s.execute(select(Channel).where(Channel.monitoring.is_(True)))).scalars().all()]
        for cid in ids:
            await self.start(cid, persist=False)

    async def start(self, channel_id: int, persist: bool = True) -> None:
        if channel_id in self.supervisors and not self.supervisors[channel_id].done():
            return
        active = sum(1 for t in self.supervisors.values() if not t.done())
        if active >= self.settings.max_streams:
            raise RuntimeError(f"max_streams ({self.settings.max_streams}) reached")
        if persist:
            async with session_scope() as s:
                ch = await s.get(Channel, channel_id)
                if ch is None:
                    raise ValueError("channel not found")
                ch.monitoring = True
        self.supervisors[channel_id] = asyncio.create_task(self._supervise(channel_id), name=f"supervisor-{channel_id}")
        bus.publish("channel.monitoring", channel_id=channel_id, monitoring=True)

    async def stop(self, channel_id: int, persist: bool = True) -> None:
        if persist:
            async with session_scope() as s:
                ch = await s.get(Channel, channel_id)
                if ch:
                    ch.monitoring = False
        sess = self.sessions.get(channel_id)
        if sess:
            sess.stop()
        task = self.supervisors.pop(channel_id, None)
        if task:
            if sess:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=180)
                except asyncio.TimeoutError:
                    task.cancel()
            else:
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        bus.publish("channel.monitoring", channel_id=channel_id, monitoring=False)

    async def shutdown(self) -> None:
        for cid in list(self.supervisors):
            sess = self.sessions.get(cid)
            if sess:
                sess.stop()
        tasks = list(self.supervisors.values())
        if tasks:
            await asyncio.wait(tasks, timeout=60)
        for t in tasks:
            t.cancel()
        if self._asr:
            self._asr.shutdown()

    async def check(self, channel_id: int) -> LiveStatus:
        async with session_scope() as s:
            ch = await s.get(Channel, channel_id)
            if ch is None:
                raise ValueError("channel not found")
            cfg = channel_config(ch.config)
            connector = make_connector(source_from_row(ch), self.credentials_ctx(), cfg)
        status = await connector.check_live()
        await self._store_status(channel_id, status)
        return status

    async def _store_status(self, channel_id: int, status: LiveStatus) -> None:
        d = status.to_dict()
        self.live_state[channel_id] = d
        async with session_scope() as s:
            ch = await s.get(Channel, channel_id)
            if ch:
                ch.live_state = d
                if status.display_name and ch.display_name in (ch.handle, "", None):
                    ch.display_name = status.display_name
                if status.avatar_url:
                    ch.avatar_url = status.avatar_url
        bus.publish("channel.status", channel_id=channel_id, **{k: d[k] for k in ("is_live", "title", "category", "viewer_count", "method", "error")})

    # --- supervision ------------------------------------------------------------------------
    async def _supervise(self, channel_id: int) -> None:
        backoff = 5.0
        last_stream_id: str | None = None
        while True:
            async with session_scope() as s:
                ch = await s.get(Channel, channel_id)
                if ch is None or not ch.monitoring:
                    return
                cfg = channel_config(ch.config)
                src = source_from_row(ch)
                row = {"id": ch.id, "handle": ch.handle, "platform": ch.platform, "display_name": ch.display_name, "url": ch.url}
                connector = make_connector(src, self.credentials_ctx(), cfg)
            try:
                status = await connector.check_live()
            except Exception as exc:  # noqa: BLE001
                status = LiveStatus(False, "error", error=str(exc))
            await self._store_status(channel_id, status)
            # a YouTube (or video) link that is not a live and not an upcoming premiere: process once as a VOD
            if not status.is_live and (status.extra or {}).get("is_vod") and src.kind == "video":
                if await self._run_vod_once(channel_id, row, cfg):
                    return
                continue
            if status.is_live:
                backoff = 5.0
                session = StreamSession(self, row, connector, status, cfg)
                self.sessions[channel_id] = session
                t0 = time.time()
                try:
                    await session.run()
                finally:
                    self.sessions.pop(channel_id, None)
                last_stream_id = status.stream_id or last_stream_id
                if row["platform"] in ONE_SHOT:
                    async with session_scope() as s:
                        ch = await s.get(Channel, channel_id)
                        if ch:
                            ch.monitoring = False
                    bus.publish("channel.monitoring", channel_id=channel_id, monitoring=False, reason="source finished")
                    return
                if session._stop.is_set():
                    return
                # stream dropped: retry quickly first (reconnect), then fall back to polling
                if time.time() - t0 < 30:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 120)
                else:
                    await asyncio.sleep(5)
                continue
            if status.error and status.method in ("error",):
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
                continue
            await asyncio.sleep(self.settings.live_poll_interval_s if row["platform"] != "youtube" or not self.settings.youtube_api_key
                                else max(self.settings.live_poll_interval_s, 300))

    async def _run_vod_once(self, channel_id: int, row: dict, cfg: dict) -> bool:
        """Process a non-live video once (extract clips) and stop monitoring. Returns True when handled."""
        vod_src = ParsedSource("vod", row["url"], "vod", display_name=row["display_name"], url=row["url"])
        connector = make_connector(vod_src, self.credentials_ctx(), {**cfg, "realtime": False, "channel_id": channel_id})
        try:
            status = await connector.check_live()
        except Exception as exc:  # noqa: BLE001
            status = LiveStatus(False, "error", error=str(exc))
        if not status.is_live:
            await self._store_status(channel_id, status)
            return False  # not playable yet; keep polling
        session = StreamSession(self, row, connector, status, cfg)
        self.sessions[channel_id] = session
        try:
            await session.run()
        finally:
            self.sessions.pop(channel_id, None)
        async with session_scope() as s:
            ch = await s.get(Channel, channel_id)
            if ch:
                ch.monitoring = False
        bus.publish("channel.monitoring", channel_id=channel_id, monitoring=False, reason="vídeo procesado")
        return True

    def snapshot(self) -> dict[int, dict[str, Any]]:
        return {cid: s.snapshot() for cid, s in self.sessions.items()}


_manager: StreamManager | None = None


def get_manager() -> StreamManager:
    if _manager is None:
        raise RuntimeError("manager not initialised")
    return _manager


def set_manager(m: StreamManager) -> None:
    global _manager
    _manager = m
