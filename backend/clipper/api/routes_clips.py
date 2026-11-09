"""Moments (detected candidates), clips (library/editor) and feedback endpoints."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, select, true

from clipper.api.serializers import clip_out, moment_out
from clipper.clips.service import create_clip_from_moment, enqueue_render, plan_for
from clipper.core.appsettings import channel_config, store
from clipper.core.events import bus
from clipper.db.models import Channel, Clip, Feedback, Moment
from clipper.db.session import session_scope
from clipper.editor.edit import ClipEdit, default_edit, diff_summary

router = APIRouter(prefix="/api", tags=["clips"])

APPROVE_REASONS = ["gracioso", "buen_gancho", "historia_interesante", "reaccion", "polemica", "revelacion", "buen_contexto", "util"]
REJECT_REASONS = ["aburrido", "mal_cortado", "demasiado_largo", "demasiado_corto", "falta_contexto", "audio_malo",
                  "duplicado", "sin_sentido", "no_publicable"]


class FeedbackIn(BaseModel):
    decision: str = "rate"  # approve | reject | rate
    rating: int | None = Field(default=None, ge=1, le=5)
    mediocre: bool = False
    needs_recut: bool = False
    reasons: list[str] = Field(default_factory=list)
    render: bool = True  # review mode: render the final clip after approval
    edit: dict[str, Any] | None = None


async def _channels(s, ids) -> dict[int, Channel]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (await s.execute(select(Channel).where(Channel.id.in_(ids)))).scalars().all()
    return {c.id: c for c in rows}


# --- moments ------------------------------------------------------------------------------
@router.get("/moments")
async def list_moments(status: str | None = None, channel_id: int | None = None, min_score: float | None = None,
                       priority: str | None = None, category: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    async with session_scope() as s:
        conds = []
        if status:
            conds.append(Moment.status.in_(status.split(",")))
        if channel_id:
            conds.append(Moment.channel_id == channel_id)
        if min_score is not None:
            conds.append(Moment.score >= min_score)
        if priority:
            conds.append(Moment.priority.in_(priority.split(",")))
        if category:
            conds.append(Moment.category == category)
        where = and_(*conds) if conds else true()
        total = (await s.execute(select(func.count(Moment.id)).where(where))).scalar() or 0
        rows = (await s.execute(select(Moment).where(where).order_by(desc(Moment.created_at)).limit(limit).offset(offset))).scalars().all()
        chans = await _channels(s, [m.channel_id for m in rows])
        return {"total": total, "items": [moment_out(m, chans.get(m.channel_id)) for m in rows]}


@router.get("/moments/{mid}")
async def get_moment(mid: int) -> dict[str, Any]:
    async with session_scope() as s:
        m = await s.get(Moment, mid)
        if m is None:
            raise HTTPException(404)
        clips = (await s.execute(select(Clip.id).where(Clip.moment_id == mid))).scalars().all()
        out = moment_out(m, await s.get(Channel, m.channel_id), full=True)
        out["clip_ids"] = list(clips)
        return out


async def _record_feedback(*, moment: Moment | None, clip: Clip | None, body: FeedbackIn, modifications: dict[str, Any]) -> int:
    m = moment
    snap = {}
    if m is not None:
        snap = {"transcript": m.transcript, "signals": m.signals, "category": m.category, "score": m.score,
                "subscores": m.subscores, "duration": m.end - m.start, "title": m.title, "words": m.words,
                "source_path": m.source_path, "model_version": m.model_version}
    async with session_scope() as s:
        fb = Feedback(moment_id=m.id if m else None, clip_id=clip.id if clip else None,
                      channel_id=(m.channel_id if m else clip.channel_id if clip else None), decision=body.decision,
                      rating=body.rating, mediocre=body.mediocre, needs_recut=body.needs_recut, reasons=body.reasons,
                      modifications=modifications, snapshot=snap)
        s.add(fb)
        await s.flush()
        fid = fb.id
    bus.publish("feedback.recorded", feedback_id=fid, moment_id=m.id if m else None, decision=body.decision, rating=body.rating)
    return fid


@router.post("/moments/{mid}/feedback")
async def moment_feedback(mid: int, body: FeedbackIn) -> dict[str, Any]:
    async with session_scope() as s:
        m = await s.get(Moment, mid)
        if m is None:
            raise HTTPException(404)
        ch = await s.get(Channel, m.channel_id)
        cfg = channel_config(ch.config if ch else {})
        if body.decision == "approve":
            m.status = "approved"
        elif body.decision == "reject":
            m.status = "rejected"
        original = default_edit(m, cfg, store.get("captions")).model_dump()
    mods = diff_summary(original, body.edit) if body.edit else {}
    fid = await _record_feedback(moment=m, clip=None, body=body, modifications=mods)
    clip_id = None
    if body.decision == "approve" and body.render:
        try:
            clip_id = await create_clip_from_moment(mid, body.edit)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"feedback_id": fid, "clip_id": clip_id}


@router.post("/moments/{mid}/clip")
async def moment_to_clip(mid: int, render: bool = True) -> dict[str, Any]:
    try:
        return {"clip_id": await create_clip_from_moment(mid, enqueue=render)}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/feedback/reasons")
async def feedback_reasons() -> dict[str, list[str]]:
    return {"approve": APPROVE_REASONS, "reject": REJECT_REASONS}


# --- clips ---------------------------------------------------------------------------------
@router.get("/clips")
async def list_clips(status: str | None = None, channel_id: int | None = None, platform: str | None = None,
                     category: str | None = None, review: str | None = None, min_score: float | None = None,
                     min_duration: float | None = None, max_duration: float | None = None, since: float | None = None,
                     until: float | None = None, q: str | None = None, limit: int = 60, offset: int = 0) -> dict[str, Any]:
    async with session_scope() as s:
        query = select(Clip, Moment, Channel).outerjoin(Moment, Clip.moment_id == Moment.id).outerjoin(Channel, Clip.channel_id == Channel.id)
        conds = []
        if status:
            conds.append(Clip.status.in_(status.split(",")))
        if channel_id:
            conds.append(Clip.channel_id == channel_id)
        if platform:
            conds.append(Channel.platform == platform)
        if category:
            conds.append(Moment.category == category)
        if review:
            conds.append(Clip.review.in_(review.split(",")))
        if min_score is not None:
            conds.append(Moment.score >= min_score)
        if min_duration is not None:
            conds.append(Clip.duration >= min_duration)
        if max_duration is not None:
            conds.append(Clip.duration <= max_duration)
        if since:
            conds.append(Clip.created_at >= since)
        if until:
            conds.append(Clip.created_at <= until)
        if q:
            conds.append(Clip.title.ilike(f"%{q}%"))
        if conds:
            query = query.where(and_(*conds))
        total = (await s.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
        rows = (await s.execute(query.order_by(desc(Clip.created_at)).limit(limit).offset(offset))).all()
        return {"total": total, "items": [clip_out(c, m, ch) for c, m, ch in rows]}


@router.get("/clips/{cid}")
async def get_clip(cid: int) -> dict[str, Any]:
    async with session_scope() as s:
        c = await s.get(Clip, cid)
        if c is None:
            raise HTTPException(404)
        m = await s.get(Moment, c.moment_id) if c.moment_id else None
        ch = await s.get(Channel, c.channel_id) if c.channel_id else None
        return clip_out(c, m, ch, full=True)


class ClipPatch(BaseModel):
    title: str | None = None
    edit: dict[str, Any] | None = None


@router.patch("/clips/{cid}")
async def patch_clip(cid: int, body: ClipPatch) -> dict[str, Any]:
    async with session_scope() as s:
        c = await s.get(Clip, cid)
        if c is None:
            raise HTTPException(404)
        if body.title is not None:
            c.title = body.title
        if body.edit is not None:
            try:
                merged = {**c.edit, **body.edit}
                ClipEdit(**merged)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(422, f"edición inválida: {exc}") from exc
            c.edit = merged
            c.updated_at = time.time()
        m = await s.get(Moment, c.moment_id) if c.moment_id else None
        ch = await s.get(Channel, c.channel_id) if c.channel_id else None
        return clip_out(c, m, ch, full=True)


@router.post("/clips/{cid}/render")
async def render_clip(cid: int, preview: bool = False) -> dict[str, Any]:
    try:
        job = await enqueue_render(cid, preview=preview)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"job_id": job}


@router.post("/clips/{cid}/plan")
async def compute_plan(cid: int) -> dict[str, Any]:
    """Compute (and cache in the clip edit) the reframing plan for the current trim/mode, for the editor preview."""
    async with session_scope() as s:
        c = await s.get(Clip, cid)
        if c is None:
            raise HTTPException(404)
        m = await s.get(Moment, c.moment_id) if c.moment_id else None
        if m is None or not m.source_path or not Path(m.source_path).exists():
            raise HTTPException(409, "source media not available")
        edit = ClipEdit(**c.edit)
        source, category = m.source_path, m.category
    plan = await plan_for(edit, source, category)
    async with session_scope() as s:
        c = await s.get(Clip, cid)
        if c is not None:
            c.edit = {**c.edit, "reframe": edit.reframe.model_dump()}
    return {"plan": plan.to_dict(), "reframe": edit.reframe.model_dump()}


@router.post("/clips/{cid}/feedback")
async def clip_feedback(cid: int, body: FeedbackIn) -> dict[str, Any]:
    async with session_scope() as s:
        c = await s.get(Clip, cid)
        if c is None:
            raise HTTPException(404)
        m = await s.get(Moment, c.moment_id) if c.moment_id else None
        if body.decision in ("approve", "reject"):
            c.review = "approved" if body.decision == "approve" else "rejected"
        mods: dict[str, Any] = {}
        if m is not None:
            ch = await s.get(Channel, m.channel_id)
            original = default_edit(m, channel_config(ch.config if ch else {}), store.get("captions")).model_dump()
            mods = diff_summary(original, c.edit or {})
    fid = await _record_feedback(moment=m, clip=c, body=body, modifications=mods)
    return {"feedback_id": fid}


@router.delete("/clips/{cid}", status_code=204)
async def delete_clip(cid: int) -> None:
    async with session_scope() as s:
        c = await s.get(Clip, cid)
        if c is None:
            return
        for p in (c.output_path, c.thumbnail_path):
            if p:
                Path(p).unlink(missing_ok=True)
        await s.delete(c)


@router.get("/clips/{cid}/download")
async def download_clip(cid: int):
    async with session_scope() as s:
        c = await s.get(Clip, cid)
        if c is None or not c.output_path or not Path(c.output_path).exists():
            raise HTTPException(404, "clip not rendered")
        safe = "".join(ch for ch in (c.title or f"clip_{cid}") if ch.isalnum() or ch in " -_")[:60].strip() or f"clip_{cid}"
        return FileResponse(c.output_path, media_type="video/mp4", filename=f"{safe}.mp4")
