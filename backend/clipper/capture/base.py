"""Capture abstractions shared by all source types."""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from clipper.buffer.rolling import Segment

SegmentCallback = Callable[[Segment], Awaitable[None]]


class StreamEnded(Exception):
    """The source signalled the end of the broadcast (ENDLIST, EOF, offline)."""


class CaptureError(Exception):
    """Unrecoverable capture failure (e.g. DRM-protected playlist)."""


@dataclass
class CaptureStats:
    segments: int = 0
    duplicates_skipped: int = 0
    ads_skipped: int = 0
    gaps: int = 0
    reconnects: int = 0
    bytes: int = 0
    last_segment_at: float | None = None


class CaptureSource(abc.ABC):
    """Produces :class:`Segment` objects with monotonically increasing stream time."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.stats = CaptureStats()
        self._stopped = False

    @abc.abstractmethod
    async def run(self, on_segment: SegmentCallback) -> None:
        """Run until the stream ends (raise :class:`StreamEnded`) or :meth:`stop` is called."""

    def stop(self) -> None:
        self._stopped = True

    @property
    def wall_start(self) -> float | None:
        """Wall-clock time (epoch) corresponding to stream time 0, when known."""
        return None
