"""Platform connector interface.

Each platform gets an independent adapter. Adapters declare their real capabilities, so the
UI never pretends a platform supports something it doesn't.
"""

from __future__ import annotations

import abc
import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from clipper.capture.base import CaptureSource


@dataclass
class Capabilities:
    official_status_api: bool
    official_media_api: bool
    chat: bool
    chat_official: bool
    needs_credentials_for_status: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedSource:
    platform: str
    handle: str  # login / slug / @handle / video id / file path
    kind: str = "channel"  # channel | video | file | ingest | vod
    external_id: str | None = None
    display_name: str | None = None
    url: str = ""


@dataclass
class LiveStatus:
    is_live: bool
    method: str
    title: str | None = None
    category: str | None = None
    started_at: float | None = None
    viewer_count: int | None = None
    stream_id: str | None = None
    thumbnail_url: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    error: str | None = None
    checked_at: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChatMessage:
    ts: float  # wall clock epoch seconds
    user: str
    text: str
    platform: str
    emotes: list[str] = field(default_factory=list)
    id: str | None = None
    badges: list[str] = field(default_factory=list)


ChatCallback = Callable[[ChatMessage], Awaitable[None] | None]


class ChatClient(abc.ABC):
    """Read-only live chat client."""

    official: bool = True

    def __init__(self) -> None:
        self._stopped = False
        self.connected = False
        self.messages = 0
        self.error: str | None = None

    @abc.abstractmethod
    async def run(self, on_message: ChatCallback) -> None: ...

    def stop(self) -> None:
        self._stopped = True

    async def _emit(self, cb: ChatCallback, msg: ChatMessage) -> None:
        self.messages += 1
        res = cb(msg)
        if asyncio.iscoroutine(res):
            await res


@dataclass
class ConnectorContext:
    """Everything an adapter may need: settings + resolved credentials."""

    settings: Any
    credentials: dict[str, str] = field(default_factory=dict)
    http_proxy_env: bool = True


class Connector(abc.ABC):
    platform: str = "base"
    capabilities: Capabilities

    def __init__(self, source: ParsedSource, ctx: ConnectorContext, config: dict[str, Any] | None = None) -> None:
        self.source = source
        self.ctx = ctx
        self.config = config or {}

    @classmethod
    @abc.abstractmethod
    def parse_url(cls, url: str) -> ParsedSource | None: ...

    @abc.abstractmethod
    async def check_live(self) -> LiveStatus: ...

    @abc.abstractmethod
    async def open_capture(self, out_dir: Path) -> CaptureSource: ...

    def chat_client(self, status: LiveStatus) -> ChatClient | None:
        return None

    def capabilities_dict(self) -> dict[str, Any]:
        return self.capabilities.to_dict()


async def run_blocking(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)
