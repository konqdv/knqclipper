"""Channels / live streams endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from clipper.api.serializers import channel_out
from clipper.connectors.base import ConnectorContext
from clipper.connectors.registry import capabilities, make_connector, parse_source
from clipper.core.appsettings import CHANNEL_DEFAULTS, deep_merge
from clipper.core.config import get_settings
from clipper.db.models import Channel, StreamSession
from clipper.db.session import session_scope
from clipper.stream.manager import get_manager

router = APIRouter(prefix="/api", tags=["streams"])


class ResolveIn(BaseModel):
    url: str


class ChannelIn(BaseModel):
    url: str
    rights_confirmed: bool = False
    start: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class ChannelPatch(BaseModel):
    config: dict[str, Any] | None = None
    display_name: str | None = None
    rights_confirmed: bool | None = None


@router.get("/platforms")
async def platforms() -> dict[str, Any]:
    return {"capabilities": capabilities(), "channel_defaults": CHANNEL_DEFAULTS}


@router.post("/channels/resolve")
async def resolve(body: ResolveIn) -> dict[str, Any]:
    parsed = parse_source(body.url)
    if parsed is None:
        raise HTTPException(400, "URL no reconocida: usa un enlace de YouTube, Twitch, Kick, un archivo local o rtmp://")
    conn = make_connector(parsed, ConnectorContext(settings=get_settings()))
    status = await conn.check_live()
    return {"source": parsed.__dict__, "status": status.to_dict(), "capabilities": conn.capabilities_dict()}


@router.get("/channels")
async def list_channels() -> list[dict[str, Any]]:
    mgr = get_manager()
    async with session_scope() as s:
        rows = (await s.execute(select(Channel).order_by(Channel.created_at))).scalars().all()
        sessions = mgr.snapshot()
        return [channel_out(c, sessions.get(c.id)) for c in rows]


@router.post("/channels", status_code=201)
async def add_channel(body: ChannelIn) -> dict[str, Any]:
    parsed = parse_source(body.url)
    if parsed is None:
        raise HTTPException(400, "URL no reconocida")
    if not body.rights_confirmed and parsed.platform not in ("local", "ingest"):
        raise HTTPException(400, "Confirma que tienes permiso del creador para procesar esta transmisión")
    cfg = deep_merge(CHANNEL_DEFAULTS, body.config)
    if parsed.kind == "video":
        cfg["_kind"] = "video"
    async with session_scope() as s:
        exists = (await s.execute(select(Channel).where(Channel.platform == parsed.platform, Channel.handle == parsed.handle))).scalars().first()
        if exists:
            raise HTTPException(409, f"Ya existe ({exists.display_name})")
        ch = Channel(platform=parsed.platform, url=parsed.url or body.url, handle=parsed.handle,
                     display_name=parsed.display_name or parsed.handle, external_id=parsed.external_id,
                     rights_confirmed=body.rights_confirmed or parsed.platform in ("local", "ingest"), config=cfg)
        s.add(ch)
        await s.flush()
        cid = ch.id
    mgr = get_manager()
    try:
        await mgr.check(cid)
    except Exception:  # status is informative only at creation time
        pass
    if body.start:
        try:
            await mgr.start(cid)
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from exc
    async with session_scope() as s:
        return channel_out(await s.get(Channel, cid))


@router.patch("/channels/{cid}")
async def patch_channel(cid: int, body: ChannelPatch) -> dict[str, Any]:
    async with session_scope() as s:
        ch = await s.get(Channel, cid)
        if ch is None:
            raise HTTPException(404)
        if body.config is not None:
            ch.config = deep_merge(ch.config or {}, body.config)
            sess = get_manager().sessions.get(cid)
            if sess:  # live changes (mode, sensitivity, min_score...) apply immediately
                sess.cfg.update(body.config)
        if body.display_name:
            ch.display_name = body.display_name
        if body.rights_confirmed is not None:
            ch.rights_confirmed = body.rights_confirmed
        return channel_out(ch, get_manager().snapshot().get(cid))


@router.delete("/channels/{cid}", status_code=204)
async def delete_channel(cid: int) -> None:
    await get_manager().stop(cid)
    async with session_scope() as s:
        ch = await s.get(Channel, cid)
        if ch:
            await s.delete(ch)


@router.post("/channels/{cid}/start")
async def start_channel(cid: int) -> dict[str, Any]:
    async with session_scope() as s:
        ch = await s.get(Channel, cid)
        if ch is None:
            raise HTTPException(404)
        if not ch.rights_confirmed:
            raise HTTPException(400, "Confirma los permisos antes de monitorear")
    try:
        await get_manager().start(cid)
    except RuntimeError as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"ok": True}


@router.post("/channels/{cid}/stop")
async def stop_channel(cid: int) -> dict[str, Any]:
    await get_manager().stop(cid)
    return {"ok": True}


@router.post("/channels/{cid}/check")
async def check_channel(cid: int) -> dict[str, Any]:
    try:
        return (await get_manager().check(cid)).to_dict()
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/sessions")
async def list_sessions(channel_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    async with session_scope() as s:
        q = select(StreamSession).order_by(desc(StreamSession.started_at)).limit(limit)
        if channel_id:
            q = q.where(StreamSession.channel_id == channel_id)
        rows = (await s.execute(q)).scalars().all()
        return [{"id": r.id, "channel_id": r.channel_id, "status": r.status, "title": r.title,
                 "platform_category": r.platform_category, "detected_category": r.detected_category,
                 "secondary_categories": r.secondary_categories, "started_at": r.started_at, "ended_at": r.ended_at,
                 "segments": r.segments_received, "seconds": r.seconds_captured} for r in rows]
