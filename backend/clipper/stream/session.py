"""StreamSession: one live capture wired through buffer → analysis → highlight engine → moments.

Capture, analysis and evaluation run as independent tasks connected by bounded queues; a slow
analysis never blocks segment download (it degrades the visual sampling rate instead).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from clipper.analysis.chat import ChatAnalyzer
from clipper.analysis.classifier import ContentClassifier
from clipper.analysis.context import ContextAnalyzer
from clipper.analysis.emotion import EmotionDetector
from clipper.analysis.engine import HighlightEngine, MomentResult, StreamAnalysis
from clipper.analysis.visual import VisualAnalyzer
from clipper.asr.streaming import StreamingTranscriber, Transcript
from clipper.audio.decode import decode_audio, decode_frames
from clipper.audio.features import FeatureTrack, compute_features
from clipper.buffer.rolling import RollingBuffer, Segment
from clipper.capture.base import CaptureError, StreamEnded
from clipper.clips.extract import extract_segments, thumbnail
from clipper.connectors.base import ChatClient, ChatMessage, Connector, LiveStatus
from clipper.core.appsettings import store
from clipper.core.events import bus
from clipper.db.models import Moment, StreamSession as SessionRow
from clipper.db.session import session_scope
from clipper.reframe.faces import FaceDetector
from clipper.storage.manager import get_storage

log = logging.getLogger(__name__)
SOURCE_MARGIN = 10.0  # seconds of extra media kept around a moment for re-trimming in the editor


class StreamSession:
    def __init__(self, manager, channel: dict[str, Any], connector: Connector, status: LiveStatus, cfg: dict[str, Any]) -> None:
        self.manager = manager
        self.channel = channel
        self.connector = connector
        self.status = status
        self.cfg = cfg
        self.session_id: int | None = None
        self.state = "starting"
        self.started_wall = time.time()
        self.first_segment_wall: float | None = None
        self.capture = None
        self.chat_client: ChatClient | None = None
        self._tasks: list[asyncio.Task] = []
        self._moment_tasks: set[asyncio.Task] = set()
        self._seg_queue: asyncio.Queue[Segment] = asyncio.Queue(maxsize=600)
        self._stop = asyncio.Event()
        self.error: str | None = None
        self.segments = 0
        self.seconds = 0.0
        self.analysis_lag_segments = 0
        settings = manager.settings
        pre, post = float(cfg["pre_seconds"]), float(cfg["post_seconds"])
        self.buffer = RollingBuffer(max_seconds=max(float(cfg["buffer_seconds"]), pre + post + 30))
        self.track = FeatureTrack()
        self.transcript = Transcript()
        self.chat = ChatAnalyzer(reaction_delay_s=settings.chat_reaction_delay_s)
        self.visual = VisualAnalyzer(FaceDetector(settings.resolved_models_dir))
        self.classifier = ContentClassifier(status.category)
        self.asr = manager.asr_engine()
        self.transcriber = StreamingTranscriber(self.asr, self.transcript, language=cfg.get("language"),
                                                on_update=self._on_transcript) if self.asr else None
        analysis = StreamAnalysis(self.buffer, self.track, EmotionDetector(self.track), self.transcript, self.chat,
                                  self.visual, self.classifier, has_asr=self.transcriber is not None)
        self.engine = HighlightEngine(
            analysis, channel_config=cfg, detection=lambda: store.get("detection"), ranking=lambda: store.get("ranker"),
            context=ContextAnalyzer(manager.llm), learner=manager.learner, creator=channel["handle"],
            platform_category=lambda: self.status.category, on_moment=self._on_moment, frame_grabber=self._grab_frame,
        )

    # --- lifecycle -----------------------------------------------------------------------
    async def run(self) -> None:
        storage = get_storage()
        async with session_scope() as s:
            row = SessionRow(channel_id=self.channel["id"], platform_stream_id=self.status.stream_id, title=self.status.title,
                             platform_category=self.status.category, status="starting")
            s.add(row)
            await s.flush()
            self.session_id = row.id
        out_dir = storage.buffer_dir(self.session_id)
        self._publish_state("starting")
        try:
            self.capture = await self.connector.open_capture(out_dir)
            if self.cfg.get("chat_enabled", True):
                self.chat_client = self.connector.chat_client(self.status)
            self._tasks = [
                asyncio.create_task(self._analysis_worker(), name=f"analysis-{self.session_id}"),
                asyncio.create_task(self._engine_loop(), name=f"engine-{self.session_id}"),
                asyncio.create_task(self._stats_loop(), name=f"stats-{self.session_id}"),
            ]
            if self.chat_client:
                self._tasks.append(asyncio.create_task(self.chat_client.run(self._on_chat), name=f"chat-{self.session_id}"))
            capture_task = asyncio.create_task(self.capture.run(self._on_segment), name=f"capture-{self.session_id}")
            stop_task = asyncio.create_task(self._stop.wait())
            done, _ = await asyncio.wait({capture_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            if stop_task in done:
                self.capture.stop()
                capture_task.cancel()
                await asyncio.gather(capture_task, return_exceptions=True)
                end_reason = "stopped"
            else:
                stop_task.cancel()
                exc = capture_task.exception()
                if exc is None or isinstance(exc, StreamEnded):
                    end_reason = f"ended: {exc}" if exc else "ended"
                else:
                    raise exc
            await self._drain(end_reason)
        except CaptureError as exc:
            self.error = str(exc)
            await self._drain(f"error: {exc}", status="error")
        except asyncio.CancelledError:
            await self._drain("cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("session %s crashed", self.session_id)
            self.error = str(exc)
            await self._drain(f"error: {exc}", status="error")

    def stop(self) -> None:
        self._stop.set()

    async def _drain(self, reason: str, status: str = "ended") -> None:
        log.info("session %s finishing (%s)", self.session_id, reason)
        self.state = "finishing"
        self._publish_state("finishing", reason=reason)
        # finish analysing what was captured
        try:
            await asyncio.wait_for(self._seg_queue.join(), timeout=120)
        except asyncio.TimeoutError:
            log.warning("analysis queue did not drain in time")
        if self.transcriber:
            self.transcriber.flush()
            await asyncio.to_thread(self.transcriber.wait_idle, 120)
        try:
            await self.engine.tick()
            await self.engine.flush()
        except Exception:
            log.exception("final evaluation failed")
        if self._moment_tasks:
            await asyncio.gather(*self._moment_tasks, return_exceptions=True)
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.chat_client:
            self.chat_client.stop()
        self.buffer.clear()
        self.state = status
        async with session_scope() as s:
            row = await s.get(SessionRow, self.session_id)
            if row:
                row.status, row.ended_at = status, time.time()
                row.segments_received, row.seconds_captured = self.segments, self.seconds
                row.stats = self.snapshot()
                cls = self.classifier.current
                if cls:
                    row.detected_category, row.secondary_categories = cls.primary, cls.secondary
        self._publish_state(status, reason=reason)

    # --- capture → analysis ------------------------------------------------------------------
    async def _on_segment(self, seg: Segment) -> None:
        self.buffer.add(seg)
        self.segments += 1
        self.seconds = seg.end
        if self.first_segment_wall is None:
            self.first_segment_wall = time.time()
            wall0 = self.capture.wall_start if self.capture and self.capture.wall_start else time.time() - seg.end
            self.chat.wall_start = wall0
            self.state = "live"
            async with session_scope() as s:
                row = await s.get(SessionRow, self.session_id)
                if row:
                    row.status, row.wall_start = "live", wall0
            self._publish_state("live")
        try:
            self._seg_queue.put_nowait(seg)
        except asyncio.QueueFull:
            log.error("analysis queue full; dropping analysis of segment %s", seg.index)

    def _process_segment(self, seg: Segment, light: bool) -> None:
        pcm = decode_audio(seg.path, expected_duration=seg.duration)
        self.track.add(compute_features(pcm, seg.start))
        if self.transcriber:
            self.transcriber.feed(pcm, seg.start)
        frames = decode_frames(seg.path, fps=0.5 if light else 2.0, width=320)
        self.visual.process(frames, seg.start, detect_faces=True)

    async def _analysis_worker(self) -> None:
        while True:
            seg = await self._seg_queue.get()
            try:
                if not seg.path.exists():
                    continue
                self.analysis_lag_segments = self._seg_queue.qsize()
                await asyncio.to_thread(self._process_segment, seg, self.analysis_lag_segments > 5)
            except Exception:
                log.exception("segment analysis failed")
            finally:
                self._seg_queue.task_done()

    async def _engine_loop(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            try:
                await self.engine.tick()
            except Exception:
                log.exception("engine tick failed")

    async def _on_chat(self, msg: ChatMessage) -> None:
        self.chat.ingest(msg)

    def _on_transcript(self, segs) -> None:
        bus.publish("stream.transcript", channel_id=self.channel["id"], session_id=self.session_id,
                    segments=[{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text, "speaker": s.speaker} for s in segs])

    async def _grab_frame(self, t: float) -> bytes | None:
        segs = self.buffer.between(t, t + 0.1)
        if not segs:
            return None
        import cv2

        frames = await asyncio.to_thread(decode_frames, segs[0].path, 1.0, 768, 1, max(0.0, t - segs[0].start))
        if not frames:
            return None
        ok, buf = cv2.imencode(".jpg", frames[0][1], [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    # --- moments ---------------------------------------------------------------------------
    async def _on_moment(self, r: MomentResult) -> int | None:
        storage = get_storage()
        segs = self.buffer.between(r.start - SOURCE_MARGIN, r.end + SOURCE_MARGIN)
        key = f"{self.session_id}_{int(r.start * 1000)}"
        src_path = storage.source_path(self.channel["id"], key)
        try:
            src_start, src_dur = await extract_segments(segs, src_path, storage.work_dir("extract"))
        except Exception as exc:
            log.error("could not extract moment media: %s", exc)
            return None
        thumb = await thumbnail(src_path, max(0.0, r.trigger_time - src_start), storage.thumb_path(f"moment_{key}"))
        words = [{**w, "s": round(w["s"] - src_start, 3), "e": round(w["e"] - src_start, 3)} for w in r.words]
        async with session_scope() as s:
            m = Moment(
                channel_id=self.channel["id"], session_id=self.session_id, status="pending", start=r.start, end=r.end,
                trigger_time=r.trigger_time, score=r.score, subscores={"values": r.subscores, "points": r.points, "weights": r.weights},
                priority=r.priority, confidence=r.confidence, category=r.category, secondary_categories=r.secondary,
                reasons=r.reasons, title=r.title, hook=r.hook, transcript=r.transcript, words=words, context=r.context,
                signals={**r.signals, "model": r.model_info, "kind": r.kind}, source_path=str(src_path), source_start=src_start,
                source_duration=src_dur, thumbnail_path=str(thumb) if thumb else None, model_version=r.model_info.get("model_version"),
            )
            s.add(m)
            await s.flush()
            mid = m.id
            sup = r.model_info.get("supersedes")
            if sup:
                old = await s.get(Moment, sup)
                if old and old.status == "pending":
                    old.status = "discarded"
        bus.publish("moment.detected", moment_id=mid, channel_id=self.channel["id"], score=r.score, priority=r.priority,
                    title=r.title, category=r.category, duration=round(r.duration, 1))
        if self.cfg.get("mode") == "autopilot" and r.score >= float(self.cfg.get("min_score", 75)):
            from clipper.clips.service import create_clip_from_moment

            task = asyncio.create_task(create_clip_from_moment(mid))
            self._moment_tasks.add(task)
            task.add_done_callback(self._moment_tasks.discard)
        return mid

    # --- reporting --------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        cap = self.capture.stats.__dict__ if self.capture else {}
        cls = self.classifier.current
        return {
            "session_id": self.session_id, "channel_id": self.channel["id"], "state": self.state, "error": self.error,
            "segments": self.segments, "seconds_captured": round(self.seconds, 1),
            "uptime_s": round(time.time() - (self.first_segment_wall or self.started_wall), 1),
            "buffer": {"seconds": round(self.buffer.seconds, 1), "segments": len(self.buffer), "pins": len(self.buffer.pins),
                       "evicted": self.buffer.evicted},
            "capture": {k: v for k, v in cap.items() if k != "last_segment_at"},
            "last_segment_age_s": round(time.time() - cap["last_segment_at"], 1) if cap.get("last_segment_at") else None,
            "asr": {"enabled": self.transcriber is not None, "lag_s": round(self.transcriber.lag_seconds, 1) if self.transcriber else None,
                    "language": self.transcript.language, "dropped_s": round(self.transcriber.dropped_seconds, 1) if self.transcriber else 0,
                    "rtf": round(self.asr.realtime_factor, 3) if self.asr and self.asr.realtime_factor else None},
            "analysis_backlog": self._seg_queue.qsize(),
            "chat": {"connected": bool(self.chat_client and self.chat_client.connected), "messages": self.chat.total,
                     "official": self.chat_client.official if self.chat_client else None,
                     "error": self.chat_client.error if self.chat_client else None},
            "classification": cls.to_dict() if cls else None,
            "signal": self.engine.last_signal, "engine": self.engine.stats, "open_candidates": len(self.engine.open),
            "transcript_tail": self.transcript.tail_text(260),
        }

    async def _stats_loop(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(3.0)
            n += 1
            snap = self.snapshot()
            bus.publish("stream.stats", **snap)
            if n % 10 == 0:
                async with session_scope() as s:
                    row = await s.get(SessionRow, self.session_id)
                    if row:
                        row.segments_received, row.seconds_captured, row.stats = self.segments, self.seconds, snap
                        if self.classifier.current:
                            row.detected_category = self.classifier.current.primary
                            row.secondary_categories = self.classifier.current.secondary

    def _publish_state(self, state: str, **extra: Any) -> None:
        bus.publish("stream.state", channel_id=self.channel["id"], session_id=self.session_id, state=state, **extra)


Path  # noqa: B018 - re-exported for typing in callers
