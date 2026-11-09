"""Clip service: create clips from moments, render them as jobs, keep the DB and the UI in sync."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from clipper.audio.decode import decode_audio, probe_video
from clipper.clips.extract import thumbnail
from clipper.clips.render import RenderSpec, render
from clipper.core.appsettings import channel_config, store
from clipper.core.config import get_settings
from clipper.core.events import bus
from clipper.core.jobs import PermanentJobError, get_queue
from clipper.db.models import Channel, Clip, Moment
from clipper.db.session import session_scope
from clipper.editor.edit import ClipEdit, default_edit
from clipper.reframe.planner import ReframePlan, ReframePlanner
from clipper.storage.manager import get_storage

log = logging.getLogger(__name__)


async def create_clip_from_moment(moment_id: int, edit: dict[str, Any] | None = None, enqueue: bool = True) -> int:
    async with session_scope() as s:
        m = await s.get(Moment, moment_id)
        if m is None:
            raise ValueError("moment not found")
        if not m.source_path or not Path(m.source_path).exists():
            raise ValueError("the source media of this moment is no longer available")
        ch = await s.get(Channel, m.channel_id)
        cfg = channel_config(ch.config if ch else {})
        e = ClipEdit(**edit) if edit else default_edit(m, cfg, store.get("captions"))
        clip = Clip(moment_id=m.id, channel_id=m.channel_id, title=m.title, status="queued" if enqueue else "draft",
                    edit=e.model_dump())
        s.add(clip)
        await s.flush()
        clip_id = clip.id
    bus.publish("clip.created", clip_id=clip_id, moment_id=moment_id)
    if enqueue:
        await get_queue().enqueue("render", {"clip_id": clip_id})
    return clip_id


async def enqueue_render(clip_id: int, preview: bool = False) -> int:
    async with session_scope() as s:
        c = await s.get(Clip, clip_id)
        if c is None:
            raise ValueError("clip not found")
        if not preview:
            c.status, c.progress, c.error = "queued", 0.0, None
    return await get_queue().enqueue("render", {"clip_id": clip_id, "preview": preview})


def _energy_fn(src: str):
    pcm = decode_audio(src)
    if len(pcm) == 0:
        return None
    hop = 1600
    n = len(pcm) // hop
    rms = np.sqrt(np.mean(pcm[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-10)
    norm = rms / (np.percentile(rms, 95) + 1e-9)

    def at(t: float) -> float:
        i = int(t * 10)
        return float(norm[i]) if 0 <= i < len(norm) else 0.0

    return at


async def plan_for(clip_edit: ClipEdit, source: str, category: str | None) -> ReframePlan:
    key = clip_edit.plan_key()
    if clip_edit.reframe.plan and clip_edit.reframe.plan_key == key:
        return ReframePlan.from_dict(clip_edit.reframe.plan)
    settings = get_settings()

    def compute() -> ReframePlan:
        energy = _energy_fn(source)
        return ReframePlanner(settings.resolved_models_dir).plan(
            source, clip_edit.trim_start, clip_edit.trim_end, clip_edit.reframe.mode, clip_edit.reframe.manual,
            audio_energy=energy, category=category)

    plan = await asyncio.to_thread(compute)
    clip_edit.reframe.plan = plan.to_dict()
    clip_edit.reframe.plan_key = key
    return plan


async def _mark_failed(clip_id: int, error: str, preview: bool) -> None:
    if not preview:
        async with session_scope() as s:
            c = await s.get(Clip, clip_id)
            if c:
                c.status, c.error = "failed", error[-1500:]
    bus.publish("clip.failed", clip_id=clip_id, error=error[-300:], preview=preview)


async def render_job(payload: dict[str, Any]) -> None:
    clip_id = int(payload["clip_id"])
    preview = bool(payload.get("preview"))
    storage = get_storage()
    async with session_scope() as s:
        c = await s.get(Clip, clip_id)
        if c is None:
            raise PermanentJobError("clip deleted")
        m = await s.get(Moment, c.moment_id) if c.moment_id else None
        source = m.source_path if m else c.edit.get("source_path")
        category = m.category if m else None
        edit_raw = dict(c.edit)
        render_no = c.render_count + 1
        if not preview:
            c.status, c.progress = "rendering", 0.0
    if not source or not Path(source).exists():
        await _mark_failed(clip_id, "source media missing", preview)
        raise PermanentJobError("source media missing")
    bus.publish("clip.rendering", clip_id=clip_id, preview=preview)
    try:
        info = await asyncio.to_thread(probe_video, source)
        edit = ClipEdit(**edit_raw).validate_against(info["duration"] or 1e9)
        plan = await plan_for(edit, source, category)
    except Exception as exc:
        await _mark_failed(clip_id, f"reframe/plan: {exc}", preview)
        raise
    rcfg = store.get("render")
    out = storage.work_dir("previews") / f"clip_{clip_id}_preview.mp4" if preview else storage.clip_path(clip_id, render_no)
    spec = RenderSpec(
        source=Path(source), output=out, trim_start=edit.trim_start, trim_end=edit.trim_end,
        words=[w.model_dump() for w in edit.words], caption_style=edit.caption_style, plan=plan,
        title=edit.title.model_dump() if edit.title.text else None,
        watermark=edit.watermark.model_dump() if (edit.watermark.text or edit.watermark.image) else None,
        crf=int(rcfg.get("crf", 18)), preset=str(rcfg.get("preset", "medium")), audio_bitrate=str(rcfg.get("audio_bitrate", "192k")),
        fps=int(rcfg.get("fps", 30)), normalize_audio=bool(rcfg.get("normalize_audio", True)), preview=preview,
    )
    last_pub = [0.0]

    def progress(f: float) -> None:
        if time.time() - last_pub[0] > 0.5 or f >= 1.0:
            last_pub[0] = time.time()
            bus.publish("clip.progress", clip_id=clip_id, progress=round(f, 3), preview=preview)

    try:
        result = await render(spec, storage.work_dir(f"clip_{clip_id}"), progress, has_audio=info["has_audio"])
    except Exception as exc:
        await _mark_failed(clip_id, str(exc), preview)
        raise
    if preview:
        bus.publish("clip.preview_ready", clip_id=clip_id, url=storage.to_media_url(out) + f"?v={int(time.time())}")
        return
    thumb = await thumbnail(out, min(1.5, spec.duration / 3), storage.thumb_path(f"clip_{clip_id}_r{render_no}"))
    async with session_scope() as s:
        c = await s.get(Clip, clip_id)
        if c is None:
            out.unlink(missing_ok=True)
            return
        old = c.output_path
        c.output_path, c.thumbnail_path = str(out), str(thumb) if thumb else c.thumbnail_path
        c.duration, c.status, c.progress, c.error = spec.duration, "ready", 1.0, None
        c.render_seconds, c.render_count = result["seconds"], render_no
        c.edit = edit.model_dump()  # includes the cached reframe plan
        if m is not None:
            mm = await s.get(Moment, m.id)
            if mm and mm.status in ("pending", "approved"):
                mm.status = "rendered"
    if old and old != str(out):
        Path(old).unlink(missing_ok=True)
    bus.publish("clip.ready", clip_id=clip_id, url=storage.to_media_url(out), seconds=result["seconds"], encoder=result["encoder"])
