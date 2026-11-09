"""Publishing endpoints: which networks are ready, connect/disconnect accounts, publish a clip."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from clipper.core.config import get_settings
from clipper.core.credentials import get_credentials
from clipper.db.models import Channel, Clip, Moment
from clipper.db.session import session_scope
from clipper.llm.providers import make_provider
from clipper.publish.copy import generate_copy
from clipper.publish.registry import PLATFORMS, targets
from clipper.publish.service import publish_clip

router = APIRouter(prefix="/api", tags=["publish"])


async def _accounts() -> dict[str, Any]:
    creds = get_credentials()
    tiktok = await creds.get("tiktok", "refresh_token")
    meta_page = await creds.get("meta", "page_name")
    ig = await creds.get("meta", "ig_username")
    google = await creds.get("google", "refresh_token")
    return {
        "google": {"connected": bool(google)},
        "tiktok": {"connected": bool(tiktok), "open_id": bool(await creds.get("tiktok", "open_id"))},
        "meta": {"connected": bool(meta_page), "page": meta_page or None, "instagram": ig or None},
    }


@router.get("/publish/targets")
async def publish_targets() -> dict[str, Any]:
    s = get_settings()
    return {"targets": await targets(s, get_credentials()), "accounts": await _accounts(),
            "public_base_url": s.public_base_url}


@router.delete("/publish/accounts/{provider}")
async def disconnect(provider: str) -> dict[str, Any]:
    if provider not in ("tiktok", "meta", "google"):
        raise HTTPException(404)
    creds = get_credentials()
    for name in ("access_token", "refresh_token", "open_id", "expires_at", "user_token", "page_id", "page_token",
                 "page_name", "ig_user_id", "ig_username"):
        await creds.delete(provider, name)
    return {"ok": True}


class CopyIn(BaseModel):
    channel: str | None = None
    niche: str | None = None
    country: str | None = None


@router.post("/clips/{cid}/social-copy")
async def social_copy(cid: int, body: CopyIn) -> dict[str, Any]:
    async with session_scope() as s:
        clip = await s.get(Clip, cid)
        if clip is None:
            raise HTTPException(404)
        moment = await s.get(Moment, clip.moment_id) if clip.moment_id else None
        channel = await s.get(Channel, clip.channel_id) if clip.channel_id else None
        transcript = (moment.transcript if moment else "") or clip.title
        category = moment.category if moment else "general"
        hook = moment.hook if moment else None
        lang = ((moment.context or {}).get("language") if moment else None) or "es"
        creator = body.channel or (channel.display_name if channel else None)
    copy = await generate_copy(make_provider(get_settings()), transcript=transcript, title=clip.title, hook=hook,
                               category=category, channel=creator, niche=body.niche, country=body.country, lang=lang)
    return copy.to_dict()


class PublishIn(BaseModel):
    platforms: list[str]
    caption: str | None = None
    captions: dict[str, str] = Field(default_factory=dict)  # per-platform override
    hashtags: list[str] = Field(default_factory=list)
    privacy: str | None = None


@router.post("/clips/{cid}/publish", status_code=201)
async def publish(cid: int, body: PublishIn) -> dict[str, Any]:
    if not body.platforms:
        raise HTTPException(422, "elige al menos una red")
    bad = [p for p in body.platforms if p not in PLATFORMS]
    if bad:
        raise HTTPException(422, f"plataformas desconocidas: {bad}")
    try:
        created = await publish_clip(cid, body.platforms, caption=body.caption, captions=body.captions,
                                     privacy=body.privacy, hashtags=body.hashtags)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"publications": created}
