"""Live HLS capture with duplicate-free reconnection.

The platform adapters only *resolve* a playlist URL (via official APIs / streamlink / yt-dlp);
this module downloads the media segments itself so that it can:

* deduplicate by ``PROGRAM-DATE-TIME`` (preferred) or media sequence number across
  playlist reloads, URL re-resolution and reconnections;
* keep a truthful stream timeline (gaps are preserved, never compressed);
* drop server-side inserted ads (Twitch ``Amazon`` titles / ``twitch-stitched-ad`` dateranges);
* support fMP4 segments (``EXT-X-MAP``) by prepending the init section to every segment file.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from clipper.buffer.rolling import Segment
from clipper.capture.base import CaptureError, CaptureSource, SegmentCallback, StreamEnded
from clipper.capture.m3u8 import MediaSegment, Playlist, choose_variant, parse_playlist

log = logging.getLogger(__name__)

Resolver = Callable[[], Awaitable[str]]
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def is_twitch_ad(seg: MediaSegment, pl: Playlist) -> bool:
    if seg.title and "Amazon" in seg.title:
        return True
    for dr in pl.dateranges:
        if (dr.classname == "twitch-stitched-ad" or dr.id.startswith("stitched-ad-")) and dr.contains(seg.pdt):
            return True
    return False


class HLSCapture(CaptureSource):
    def __init__(
        self,
        out_dir: Path,
        resolver: Resolver,
        *,
        headers: dict[str, str] | None = None,
        max_height: int = 1080,
        filter_ads: bool = False,
        live_edge_segments: int = 3,
        offline_timeout_s: float = 90.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(out_dir)
        self._resolver = resolver
        self._headers = {"User-Agent": USER_AGENT, **(headers or {})}
        self._max_height = max_height
        self._filter_ads = filter_ads
        self._live_edge = live_edge_segments
        self._offline_timeout = offline_timeout_s
        self._client = client
        self._own_client = client is None
        # dedup state
        self._seen_seq: deque[int] = deque(maxlen=4096)
        self._seen_set: set[int] = set()
        self._last_pdt: float | None = None
        self._last_seq: int | None = None
        # timeline state
        self._pdt0: float | None = None
        self._next_start = 0.0
        self._index = 0
        self._init_cache: dict[str, bytes] = {}
        self._wall_start: float | None = None
        self._first_fetch = True

    @property
    def wall_start(self) -> float | None:
        return self._pdt0 if self._pdt0 is not None else self._wall_start

    # ------------------------------------------------------------------------------
    async def _get(self, url: str) -> httpx.Response:
        assert self._client is not None
        r = await self._client.get(url, headers=self._headers, timeout=15.0, follow_redirects=True)
        return r

    async def _resolve_media_playlist(self) -> str:
        url = await self._resolver()
        r = await self._get(url)
        r.raise_for_status()
        pl = parse_playlist(r.text, str(r.url))
        if pl.is_master:
            v = choose_variant(pl.variants, self._max_height)
            if v is None:
                raise CaptureError("master playlist has no variants")
            log.info("HLS variant selected: %sx%s @%s bw=%s", v.width, v.height, v.frame_rate, v.bandwidth)
            return v.uri
        return str(r.url)

    def _is_duplicate(self, seg: MediaSegment) -> bool:
        if seg.pdt is not None and self._last_pdt is not None:
            return seg.pdt <= self._last_pdt + 0.05
        if self._last_seq is not None and seg.sequence < self._last_seq - 1000:
            # numbering reset by the server (new origin) and no PDT to compare: accept
            self._seen_seq.clear()
            self._seen_set.clear()
            self._last_seq = None
            return False
        return seg.sequence in self._seen_set

    def _mark_seen(self, seg: MediaSegment) -> None:
        if len(self._seen_seq) == self._seen_seq.maxlen:
            self._seen_set.discard(self._seen_seq[0])
        self._seen_seq.append(seg.sequence)
        self._seen_set.add(seg.sequence)
        self._last_seq = seg.sequence if self._last_seq is None else max(self._last_seq, seg.sequence)
        if seg.pdt is not None:
            self._last_pdt = seg.pdt if self._last_pdt is None else max(self._last_pdt, seg.pdt)

    def _timeline_start(self, seg: MediaSegment, target: float) -> tuple[float, bool]:
        """Return (stream_time, gap_detected) for a new segment."""
        if seg.pdt is not None:
            if self._pdt0 is None:
                self._pdt0 = seg.pdt
            t = seg.pdt - self._pdt0
            gap = t > self._next_start + max(1.0, target)
            return max(t, self._next_start), gap
        if self._last_seq is not None and seg.sequence > self._last_seq + 1:
            missing = seg.sequence - self._last_seq - 1
            return self._next_start + missing * target, True
        return self._next_start, False

    async def _download(self, seg: MediaSegment) -> bytes:
        r = await self._get(seg.uri)
        r.raise_for_status()
        data = r.content
        if seg.map_uri:
            init = self._init_cache.get(seg.map_uri)
            if init is None:
                ri = await self._get(seg.map_uri)
                ri.raise_for_status()
                init = ri.content
                self._init_cache = {seg.map_uri: init}
            data = init + data
        return data

    # ------------------------------------------------------------------------------
    async def run(self, on_segment: SegmentCallback) -> None:
        if self._own_client:
            self._client = httpx.AsyncClient(http2=False, trust_env=True)
        try:
            await self._loop(on_segment)
        finally:
            if self._own_client and self._client is not None:
                await self._client.aclose()

    async def _loop(self, on_segment: SegmentCallback) -> None:
        media_url = await self._resolve_media_playlist()
        last_progress = time.time()
        backoff = 1.0
        while not self._stopped:
            try:
                r = await self._get(media_url)
                if r.status_code in (401, 403, 404, 410):
                    log.info("playlist returned %s, re-resolving", r.status_code)
                    self.stats.reconnects += 1
                    media_url = await self._resolve_media_playlist()
                    continue
                r.raise_for_status()
                pl = parse_playlist(r.text, str(r.url))
                backoff = 1.0
            except (httpx.HTTPError, ValueError) as exc:
                if time.time() - last_progress > self._offline_timeout:
                    raise StreamEnded(f"no media for {self._offline_timeout:.0f}s: {exc}") from exc
                log.warning("playlist fetch failed (%s); retrying in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15)
                try:
                    self.stats.reconnects += 1
                    media_url = await self._resolve_media_playlist()
                except Exception as rexc:  # noqa: BLE001
                    log.warning("re-resolve failed: %s", rexc)
                continue

            segments = pl.segments
            if self._first_fetch and segments:
                segments = segments[-self._live_edge:]
                self._first_fetch = False
                self._wall_start = time.time() - sum(s.duration for s in segments)
            new = 0
            for seg in segments:
                if self._stopped:
                    return
                if seg.key_method:
                    raise CaptureError(f"encrypted HLS ({seg.key_method}) is not supported")
                if self._is_duplicate(seg):
                    self.stats.duplicates_skipped += 1
                    continue
                if self._filter_ads and is_twitch_ad(seg, pl):
                    self._mark_seen(seg)
                    self.stats.ads_skipped += 1
                    continue
                try:
                    data = await self._download(seg)
                except httpx.HTTPError as exc:
                    log.warning("segment %s download failed: %s", seg.sequence, exc)
                    break  # retry on next reload; not marked as seen
                start, gap = self._timeline_start(seg, pl.target_duration)
                if gap:
                    self.stats.gaps += 1
                self._mark_seen(seg)
                ext = ".mp4" if seg.map_uri else ".ts"
                path = self.out_dir / f"seg_{self._index:07d}{ext}"
                path.write_bytes(data)
                out = Segment(
                    index=self._index, start=start, duration=seg.duration, path=path,
                    media_sequence=seg.sequence, pdt=seg.pdt, discontinuity=seg.discontinuity or gap,
                )
                self._index += 1
                self._next_start = out.end
                self.stats.segments += 1
                self.stats.bytes += len(data)
                self.stats.last_segment_at = time.time()
                last_progress = time.time()
                new += 1
                await on_segment(out)
            if pl.ended:
                raise StreamEnded("playlist ended (EXT-X-ENDLIST)")
            if new == 0 and time.time() - last_progress > self._offline_timeout:
                raise StreamEnded(f"no new segments for {self._offline_timeout:.0f}s")
            await asyncio.sleep(pl.target_duration / 2 if new == 0 else max(0.5, pl.target_duration * 0.75))
