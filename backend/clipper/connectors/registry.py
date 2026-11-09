"""Connector registry: URL → platform adapter."""

from __future__ import annotations

from typing import Any

from clipper.connectors.base import Connector, ConnectorContext, ParsedSource
from clipper.connectors.kick import KickConnector
from clipper.connectors.local import IngestConnector, LocalFileConnector, VodConnector
from clipper.connectors.twitch import TwitchConnector
from clipper.connectors.youtube import YouTubeConnector

# order matters: specific platforms before the generic VOD fallback
CONNECTORS: list[type[Connector]] = [
    TwitchConnector, YouTubeConnector, KickConnector, LocalFileConnector, IngestConnector, VodConnector,
]
BY_PLATFORM: dict[str, type[Connector]] = {c.platform: c for c in CONNECTORS}


def parse_source(url: str) -> ParsedSource | None:
    for cls in CONNECTORS:
        parsed = cls.parse_url(url)
        if parsed is not None:
            return parsed
    return None


def make_connector(source: ParsedSource, ctx: ConnectorContext, config: dict[str, Any] | None = None) -> Connector:
    cls = BY_PLATFORM.get(source.platform)
    if cls is None:
        raise ValueError(f"unknown platform {source.platform}")
    return cls(source, ctx, config)


def capabilities() -> dict[str, Any]:
    return {c.platform: c.capabilities.to_dict() for c in CONNECTORS}
