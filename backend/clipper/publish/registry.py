"""Registry of social publishers."""

from __future__ import annotations

from typing import Any

from clipper.publish.base import PublishContext, Publisher
from clipper.publish.facebook import FacebookPublisher
from clipper.publish.instagram import InstagramPublisher
from clipper.publish.tiktok import TikTokPublisher
from clipper.publish.youtube import YouTubePublisher

# display order
PLATFORMS = ["shorts", "tiktok", "reels", "feed", "fb_reels"]


def make_publisher(platform: str, ctx: PublishContext) -> Publisher:
    if platform == "shorts":
        return YouTubePublisher(ctx)
    if platform == "tiktok":
        return TikTokPublisher(ctx)
    if platform in ("reels", "feed"):
        return InstagramPublisher(ctx, platform)
    if platform == "fb_reels":
        return FacebookPublisher(ctx)
    raise ValueError(f"unknown platform {platform}")


def context(settings, credentials) -> PublishContext:
    return PublishContext(settings=settings, credentials=credentials, public_base_url=settings.public_base_url)


async def targets(settings, credentials) -> list[dict[str, Any]]:
    ctx = context(settings, credentials)
    return [await make_publisher(p, ctx).readiness() for p in PLATFORMS]
