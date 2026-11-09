"""Publish service: turns a clip + chosen networks into Publication rows and background publish jobs.

A successful publish also creates the Publication/metadata the Social Performance analyzer already uses,
so published clips show up in Analytics automatically.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from clipper.core.config import get_settings
from clipper.core.credentials import get_credentials
from clipper.core.events import bus
from clipper.core.jobs import PermanentJobError, get_queue
from clipper.db.models import Clip, Publication
from clipper.db.session import session_scope
from clipper.publish.base import PublishRequest, build_caption
from clipper.publish.registry import PLATFORMS, context, make_publisher
from clipper.storage.manager import get_storage

log = logging.getLogger(__name__)
CAPTION_LIMIT = {"shorts": 4900, "tiktok": 2000, "reels": 2100, "feed": 2100, "fb_reels": 2100}


async def publish_clip(clip_id: int, platforms: list[str], *, caption: str | None = None, privacy: str | None = None,
                       hashtags: list[str] | None = None, captions: dict[str, str] | None = None) -> list[dict[str, Any]]:
    bad = [p for p in platforms if p not in PLATFORMS]
    if bad:
        raise ValueError(f"plataformas desconocidas: {bad}")
    async with session_scope() as s:
        clip = await s.get(Clip, clip_id)
        if clip is None:
            raise ValueError("clip not found")
        if clip.status != "ready" or not clip.output_path or not Path(clip.output_path).exists():
            raise ValueError("el clip debe estar renderizado antes de publicarse")
        created = []
        for p in platforms:
            pub = Publication(clip_id=clip_id, platform=p, status="queued",
                              log={"caption": (captions or {}).get(p) or caption, "privacy": privacy, "hashtags": hashtags or []})
            s.add(pub)
            await s.flush()
            created.append({"id": pub.id, "platform": p})
    for item in created:
        await get_queue().enqueue("publish", {"publication_id": item["id"]})
    bus.publish("publish.queued", clip_id=clip_id, platforms=platforms)
    return created


async def publish_job(payload: dict[str, Any]) -> None:
    pub_id = int(payload["publication_id"])
    settings = get_settings()
    storage = get_storage()
    async with session_scope() as s:
        pub = await s.get(Publication, pub_id)
        if pub is None:
            raise PermanentJobError("publication deleted")
        clip = await s.get(Clip, pub.clip_id)
        if clip is None or not clip.output_path or not Path(clip.output_path).exists():
            pub.status, pub.error = "failed", "el clip ya no está disponible"
            raise PermanentJobError("clip missing")
        platform = pub.platform
        opts = dict(pub.log or {})
        title = clip.title
        path = Path(clip.output_path)
        pub.status = "uploading"
    bus.publish("publish.uploading", publication_id=pub_id, platform=platform)

    media_url = None
    if settings.public_base_url:
        rel = storage.to_media_url(path)
        if rel:
            media_url = settings.public_base_url.rstrip("/") + rel
    caption = build_caption(title, opts.get("caption"), opts.get("hashtags"), CAPTION_LIMIT.get(platform, 2000))
    ctx = context(settings, get_credentials())
    publisher = make_publisher(platform, ctx)
    req = PublishRequest(media_path=path, media_url=media_url, caption=caption, title=title,
                         privacy=opts.get("privacy") or publisher.capabilities.default_privacy)
    try:
        result = await publisher.publish(req)
    except Exception as exc:  # noqa: BLE001
        async with session_scope() as s:
            pub = await s.get(Publication, pub_id)
            if pub:
                pub.status, pub.error = "failed", str(exc)[:1000]
        bus.publish("publish.failed", publication_id=pub_id, platform=platform, error=str(exc)[:300])
        raise
    async with session_scope() as s:
        pub = await s.get(Publication, pub_id)
        if pub is None:
            return
        pub.status = "published" if result.status == "published" else ("draft" if result.status == "draft" else "failed")
        pub.external_id = result.external_id
        pub.url = result.url
        pub.error = None if result.ok else result.detail
        pub.posted_at = time.time() if result.ok else None
        pub.log = {**(pub.log or {}), "detail": result.detail, **result.log}
    bus.publish("publish.done" if result.ok else "publish.failed", publication_id=pub_id, platform=platform,
                status=result.status, url=result.url, detail=result.detail)
