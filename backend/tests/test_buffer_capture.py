"""Rolling buffer, HLS parsing and live HLS capture with dedup/ads/reconnection."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from clipper.buffer.rolling import RollingBuffer, Segment
from clipper.capture.base import StreamEnded
from clipper.capture.ffmpeg_segmenter import FFmpegSegmenterCapture
from clipper.capture.hls import HLSCapture
from clipper.capture.m3u8 import choose_variant, parse_playlist


def _seg(i: int, d: float = 2.0, tmp: Path | None = None) -> Segment:
    p = (tmp or Path("/tmp")) / f"s{i}.ts"
    p.write_bytes(b"x")
    return Segment(i, i * d, d, p)


def test_buffer_evicts_old_segments_and_deletes_files(tmp_path):
    buf = RollingBuffer(max_seconds=10)
    segs = [_seg(i, tmp=tmp_path) for i in range(12)]
    for s in segs:
        buf.add(s)
    assert buf.seconds <= 12
    assert not segs[0].path.exists() and segs[-1].path.exists()
    assert buf.evicted == len(segs) - len(buf)


def test_buffer_pin_keeps_media_until_released(tmp_path):
    buf = RollingBuffer(max_seconds=6)
    for i in range(4):
        buf.add(_seg(i, tmp=tmp_path))
    tok = buf.pin(2.0)  # a candidate needs media from t=2s
    for i in range(4, 20):
        buf.add(_seg(i, tmp=tmp_path))
    assert buf.start_time <= 2.0 and len(buf.between(2.0, 4.0)) == 1
    buf.unpin(tok)
    assert buf.start_time > 2.0


def test_buffer_releases_stale_pins(tmp_path):
    buf = RollingBuffer(max_seconds=6, max_pinned_seconds=20)
    buf.add(_seg(0, tmp=tmp_path))
    buf.pin(0.0)
    for i in range(1, 30):
        buf.add(_seg(i, tmp=tmp_path))
    assert not buf.pins and buf.seconds <= 8


def test_parse_master_and_media_playlists():
    master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080,FRAME-RATE=60.000,CODECS="avc1.64002A,mp4a.40.2"
1080p60/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720,FRAME-RATE=30.000
720p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=160000,CODECS="mp4a.40.2"
audio/index.m3u8
"""
    pl = parse_playlist(master, "https://cdn.example/live/master.m3u8")
    assert pl.is_master and len(pl.variants) == 3
    assert choose_variant(pl.variants, 720).uri == "https://cdn.example/live/720p/index.m3u8"
    assert choose_variant(pl.variants, 1080).height == 1080
    media = """#EXTM3U
#EXT-X-TARGETDURATION:2
#EXT-X-MEDIA-SEQUENCE:100
#EXT-X-PROGRAM-DATE-TIME:2026-11-09T10:00:00.000Z
#EXTINF:2.000,live
s100.ts
#EXT-X-DISCONTINUITY
#EXTINF:2.000,Amazon|123
s101.ts
"""
    m = parse_playlist(media, "https://cdn.example/live/720p/index.m3u8")
    assert [s.sequence for s in m.segments] == [100, 101]
    assert m.segments[1].discontinuity and m.segments[1].pdt - m.segments[0].pdt == pytest.approx(2.0)


# --- live HLS capture against a local HTTP server ------------------------------------------------
class LiveHLS:
    """Serves a sliding-window live playlist over real TS segments; can inject ads / 403s / ENDLIST."""

    def __init__(self, seg_dir: Path, n_segments: int) -> None:
        self.seg_dir = seg_dir
        self.n = n_segments
        self.cursor = 3  # number of segments currently "published"
        self.window = 4
        self.ads: set[int] = set()
        self.forbid_next = 0
        self.ended = False
        self.pdt0 = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)
        self.requests: list[str] = []

    def playlist(self) -> str:
        first = max(0, self.cursor - self.window)
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:1", f"#EXT-X-MEDIA-SEQUENCE:{first}"]
        for i in range(first, self.cursor):
            pdt = (self.pdt0 + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
            lines += [f"#EXT-X-PROGRAM-DATE-TIME:{pdt}", f"#EXTINF:1.000,{'Amazon|ad' if i in self.ads else 'live'}", f"seg{i:03d}.ts"]
        if self.ended and self.cursor >= self.n:
            lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    def serve(self):
        state = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # noqa: N802
                state.requests.append(self.path)
                if self.path.startswith("/live.m3u8"):
                    if state.forbid_next > 0:
                        state.forbid_next -= 1
                        self.send_response(403)
                        self.end_headers()
                        return
                    body = state.playlist().encode()
                    state.cursor = min(state.n, state.cursor + 1)
                    if state.cursor >= state.n:
                        state.ended = True
                else:
                    f = state.seg_dir / self.path.lstrip("/")
                    if not f.exists():
                        self.send_response(404)
                        self.end_headers()
                        return
                    body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
async def test_hls_capture_dedup_ads_and_reconnect(tmp_path):
    seg_dir = tmp_path / "origin"
    seg_dir.mkdir()
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=14",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=14", "-c:v", "libx264", "-g", "25", "-c:a", "aac",
                    "-f", "segment", "-segment_time", "1", "-reset_timestamps", "1", str(seg_dir / "seg%03d.ts")], check=True)
    n = len(list(seg_dir.glob("seg*.ts")))
    live = LiveHLS(seg_dir, n)
    live.ads = {6}
    srv = live.serve()
    resolves = 0

    async def resolver() -> str:
        nonlocal resolves
        resolves += 1
        return f"http://127.0.0.1:{srv.server_port}/live.m3u8?token={resolves}"

    cap = HLSCapture(tmp_path / "buf", resolver, filter_ads=True, live_edge_segments=3, offline_timeout_s=10)
    got: list[Segment] = []

    async def on_seg(s: Segment) -> None:
        got.append(s)
        if len(got) == 4:
            live.forbid_next = 1  # token expiry -> must re-resolve without duplicating segments

    with pytest.raises(StreamEnded):
        await asyncio.wait_for(cap.run(on_seg), 60)
    srv.shutdown()
    seqs = [s.media_sequence for s in got]
    assert len(seqs) == len(set(seqs)), "duplicate segments delivered"
    assert 6 not in seqs and cap.stats.ads_skipped == 1
    assert cap.stats.duplicates_skipped > 0  # overlapping playlist reloads were deduplicated
    assert resolves >= 2  # 403 triggered a re-resolution
    starts = [s.start for s in got]
    assert starts == sorted(starts)
    # PDT timeline keeps the gap left by the skipped ad instead of compressing time
    i5, i7 = seqs.index(5), seqs.index(7)
    assert got[i7].start - got[i5].start == pytest.approx(2.0, abs=0.05)
    assert all(s.path.exists() for s in got)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
async def test_ffmpeg_segmenter_produces_contiguous_segments(tts_video, tmp_path):
    cap = FFmpegSegmenterCapture(tmp_path / "buf", str(tts_video), realtime=False, segment_seconds=2.0)
    got: list[Segment] = []

    async def on_seg(s):
        got.append(s)

    with pytest.raises(StreamEnded):
        await cap.run(on_seg)
    assert len(got) >= 3
    for a, b in zip(got, got[1:]):
        assert b.start == pytest.approx(a.end, abs=0.05)
    assert got[-1].end == pytest.approx(sum(s.duration for s in got), abs=0.1)
