"""Publishing framework: one adapter per social network.

Mirrors the capture connectors — each publisher declares what it can really do and what it needs,
so the UI never offers an action a platform does not allow. Direct public posting on TikTok, Instagram
and Facebook is gated by each platform's own app review; Instagram additionally requires the clip to be
downloadable from a public HTTPS URL. These constraints are declared, not hidden.
"""

from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PublishCapabilities:
    platform: str  # tiktok | shorts | reels | feed | fb_reels
    label: str
    network: str  # tiktok | youtube | instagram | facebook
    official_api: bool
    needs_public_url: bool  # media must be reachable at a public HTTPS URL (Instagram)
    accepts_local_file: bool
    privacy_options: list[str]  # subset of: public | private | draft | unlisted
    default_privacy: str
    requires_review_for_public: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PublishRequest:
    media_path: Path
    media_url: str | None  # absolute public URL when the server is reachable from the internet
    caption: str
    title: str
    privacy: str
    duration: float = 0.0


@dataclass
class PublishResult:
    ok: bool
    status: str  # published | draft | failed
    external_id: str | None = None
    url: str | None = None
    detail: str = ""
    log: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def failure(cls, detail: str, **log: Any) -> "PublishResult":
        return cls(ok=False, status="failed", detail=detail, log=log)


class PublishError(Exception):
    """Recoverable publishing failure with a user-facing message."""


class NotConnectedError(PublishError):
    """The platform's app credentials or the user's account connection are missing."""


class Publisher(abc.ABC):
    platform: str
    capabilities: PublishCapabilities

    def __init__(self, ctx: "PublishContext") -> None:
        self.ctx = ctx

    async def readiness(self) -> dict[str, Any]:
        """Why the platform is or isn't ready to publish right now (does not post anything)."""
        missing = await self._missing()
        ready = not missing
        if self.capabilities.needs_public_url and not self.ctx.public_base_url:
            ready = False
            missing = [*missing, "public_url"]
        return {"platform": self.platform, "ready": ready, "missing": missing,
                "capabilities": self.capabilities.to_dict()}

    @abc.abstractmethod
    async def _missing(self) -> list[str]:
        """List of missing requirements: app credential names and/or 'account' when not connected."""

    @abc.abstractmethod
    async def publish(self, req: PublishRequest) -> PublishResult: ...


@dataclass
class PublishContext:
    settings: Any
    credentials: Any  # CredentialStore
    public_base_url: str | None


def build_caption(title: str, extra: str | None, hashtags: list[str] | None, limit: int) -> str:
    parts = [p for p in [(extra or title).strip(), " ".join(f"#{h.lstrip('#')}" for h in (hashtags or []))] if p]
    text = "\n\n".join(parts).strip()
    return text[: limit - 1] + "…" if len(text) > limit else text
