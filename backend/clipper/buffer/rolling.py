"""Rolling Buffer: a time-indexed ring of media segments stored on disk.

* Keeps at least ``max_seconds`` of the most recent content.
* Older segments are evicted (file deleted) unless *pinned*: a pending highlight candidate pins
  the range it may need so that eviction never removes media that a clip still requires.
* Pins have a hard ceiling (``max_pinned_seconds``) so a stuck candidate cannot fill the disk.
* Thread-safe: segments are added from the capture task and read from analysis threads.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Segment:
    index: int  # monotonically increasing index within the session
    start: float  # stream time (s)
    duration: float
    path: Path
    media_sequence: int | None = None  # platform HLS media sequence
    pdt: float | None = None  # program-date-time, epoch seconds
    discontinuity: bool = False
    has_video: bool = True
    has_audio: bool = True
    received_at: float = field(default_factory=time.time)

    @property
    def end(self) -> float:
        return self.start + self.duration


class RollingBuffer:
    def __init__(self, max_seconds: float = 120.0, max_pinned_seconds: float = 900.0, delete_files: bool = True) -> None:
        self.max_seconds = max_seconds
        self.max_pinned_seconds = max_pinned_seconds
        self.delete_files = delete_files
        self._segs: deque[Segment] = deque()
        self._lock = threading.RLock()
        self._pins: dict[int, float] = {}  # token -> earliest stream time to keep
        self._pin_ids = itertools.count(1)
        self.evicted = 0
        self.total_added = 0

    # --- writing ----------------------------------------------------------------
    def add(self, seg: Segment) -> None:
        with self._lock:
            if self._segs and seg.start < self._segs[-1].start:
                raise ValueError("segments must be added in stream-time order")
            self._segs.append(seg)
            self.total_added += 1
            self._evict()

    def _evict(self) -> None:
        if not self._segs:
            return
        newest_end = self._segs[-1].end
        horizon = newest_end - self.max_seconds
        pin_floor = min(self._pins.values()) if self._pins else None
        if pin_floor is not None and newest_end - pin_floor > self.max_pinned_seconds:
            # a pin is holding too much media: release the oldest pins
            for tok, t in sorted(self._pins.items(), key=lambda kv: kv[1]):
                if newest_end - t <= self.max_pinned_seconds:
                    break
                log.warning("releasing stale buffer pin %s (%.0fs old)", tok, newest_end - t)
                self._pins.pop(tok)
            pin_floor = min(self._pins.values()) if self._pins else None
        keep_from = horizon if pin_floor is None else min(horizon, pin_floor)
        while len(self._segs) > 1 and self._segs[0].end <= keep_from:
            old = self._segs.popleft()
            self.evicted += 1
            if self.delete_files:
                try:
                    old.path.unlink(missing_ok=True)
                except OSError:  # pragma: no cover
                    log.warning("could not delete %s", old.path)

    # --- pins -------------------------------------------------------------------
    def pin(self, from_time: float) -> int:
        with self._lock:
            tok = next(self._pin_ids)
            self._pins[tok] = from_time
            return tok

    def unpin(self, token: int) -> None:
        with self._lock:
            self._pins.pop(token, None)
            self._evict()

    @property
    def pins(self) -> dict[int, float]:
        with self._lock:
            return dict(self._pins)

    # --- reading ----------------------------------------------------------------
    def between(self, t0: float, t1: float) -> list[Segment]:
        with self._lock:
            return [s for s in self._segs if s.end > t0 and s.start < t1]

    def latest(self) -> Segment | None:
        with self._lock:
            return self._segs[-1] if self._segs else None

    @property
    def start_time(self) -> float:
        with self._lock:
            return self._segs[0].start if self._segs else 0.0

    @property
    def end_time(self) -> float:
        with self._lock:
            return self._segs[-1].end if self._segs else 0.0

    @property
    def seconds(self) -> float:
        return self.end_time - self.start_time

    def __len__(self) -> int:
        with self._lock:
            return len(self._segs)

    def clear(self) -> None:
        with self._lock:
            for s in self._segs:
                if self.delete_files:
                    s.path.unlink(missing_ok=True)
            self._segs.clear()
            self._pins.clear()
