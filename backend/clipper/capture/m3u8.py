"""Minimal, dependency-free HLS playlist parser (RFC 8216 subset needed for live capture)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin

_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def parse_attrs(value: str) -> dict[str, str]:
    return {k: v.strip('"') for k, v in _ATTR_RE.findall(value)}


def parse_iso(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class Variant:
    uri: str
    bandwidth: int = 0
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None
    codecs: str | None = None
    name: str | None = None

    @property
    def audio_only(self) -> bool:
        return self.height is None and (self.codecs or "").startswith("mp4a") or (self.name or "").startswith("audio")


@dataclass
class DateRange:
    id: str
    start: float | None
    duration: float | None
    classname: str | None

    def contains(self, t: float | None) -> bool:
        if t is None or self.start is None:
            return False
        end = self.start + (self.duration or 0.0)
        return self.start <= t < end if self.duration else False


@dataclass
class MediaSegment:
    uri: str
    duration: float
    sequence: int
    title: str | None = None
    pdt: float | None = None
    discontinuity: bool = False
    map_uri: str | None = None
    key_method: str | None = None


@dataclass
class Playlist:
    is_master: bool = False
    variants: list[Variant] = field(default_factory=list)
    segments: list[MediaSegment] = field(default_factory=list)
    target_duration: float = 6.0
    media_sequence: int = 0
    ended: bool = False
    dateranges: list[DateRange] = field(default_factory=list)


def parse_playlist(text: str, base_url: str) -> Playlist:
    pl = Playlist()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise ValueError("not an HLS playlist")
    seq = None
    pending_inf: tuple[float, str | None] | None = None
    pending_stream: dict[str, str] | None = None
    pdt: float | None = None
    disc = False
    map_uri: str | None = None
    key_method: str | None = None
    for ln in lines[1:]:
        if ln.startswith("#EXT-X-STREAM-INF:"):
            pl.is_master = True
            pending_stream = parse_attrs(ln.split(":", 1)[1])
        elif ln.startswith("#EXT-X-TARGETDURATION:"):
            pl.target_duration = float(ln.split(":", 1)[1])
        elif ln.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            pl.media_sequence = int(ln.split(":", 1)[1])
            seq = pl.media_sequence
        elif ln.startswith("#EXTINF:"):
            body = ln.split(":", 1)[1]
            dur_s, _, title = body.partition(",")
            pending_inf = (float(dur_s), title or None)
        elif ln.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pdt = parse_iso(ln.split(":", 1)[1])
        elif ln.startswith("#EXT-X-DISCONTINUITY") and not ln.startswith("#EXT-X-DISCONTINUITY-SEQUENCE"):
            disc = True
        elif ln.startswith("#EXT-X-MAP:"):
            attrs = parse_attrs(ln.split(":", 1)[1])
            map_uri = urljoin(base_url, attrs.get("URI", "")) if attrs.get("URI") else None
        elif ln.startswith("#EXT-X-KEY:"):
            key_method = parse_attrs(ln.split(":", 1)[1]).get("METHOD")
            if key_method == "NONE":
                key_method = None
        elif ln.startswith("#EXT-X-DATERANGE:"):
            a = parse_attrs(ln.split(":", 1)[1])
            dur = a.get("DURATION") or a.get("PLANNED-DURATION")
            pl.dateranges.append(DateRange(
                id=a.get("ID", ""), start=parse_iso(a["START-DATE"]) if "START-DATE" in a else None,
                duration=float(dur) if dur else None, classname=a.get("CLASS"),
            ))
        elif ln.startswith("#EXT-X-ENDLIST"):
            pl.ended = True
        elif ln.startswith("#"):
            continue
        else:  # URI line
            uri = urljoin(base_url, ln)
            if pending_stream is not None:
                res = pending_stream.get("RESOLUTION")
                w = h = None
                if res and "x" in res:
                    w, h = (int(x) for x in res.split("x", 1))
                fr = pending_stream.get("FRAME-RATE")
                pl.variants.append(Variant(
                    uri=uri, bandwidth=int(pending_stream.get("BANDWIDTH", "0") or 0), width=w, height=h,
                    frame_rate=float(fr) if fr else None, codecs=pending_stream.get("CODECS"),
                    name=pending_stream.get("VIDEO") or pending_stream.get("STABLE-VARIANT-ID"),
                ))
                pending_stream = None
            elif pending_inf is not None:
                if seq is None:
                    seq = 0
                pl.segments.append(MediaSegment(
                    uri=uri, duration=pending_inf[0], sequence=seq, title=pending_inf[1], pdt=pdt,
                    discontinuity=disc, map_uri=map_uri, key_method=key_method,
                ))
                seq += 1
                if pdt is not None:
                    pdt += pending_inf[0]
                pending_inf = None
                disc = False
    return pl


def choose_variant(variants: list[Variant], max_height: int = 1080) -> Variant | None:
    video = [v for v in variants if not v.audio_only]
    if not video:
        return variants[0] if variants else None
    fitting = [v for v in video if (v.height or 0) <= max_height] or video
    return max(fitting, key=lambda v: ((v.height or 0), v.frame_rate or 0, v.bandwidth))
