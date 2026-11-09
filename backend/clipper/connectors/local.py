"""Local sources: files on disk, VOD/recording URLs and RTMP/SRT ingest."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from clipper.capture.base import CaptureSource
from clipper.capture.ffmpeg_segmenter import FFmpegSegmenterCapture
from clipper.capture.hls import HLSCapture
from clipper.connectors.base import Capabilities, Connector, LiveStatus, ParsedSource, run_blocking
from clipper.connectors.resolvers import ResolveError, ytdlp_download, ytdlp_info
from clipper.core.events import bus

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".ts", ".flv", ".m4v", ".avi"}


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        raise ValueError(out.stderr.strip() or "ffprobe failed")
    return json.loads(out.stdout)


class LocalFileConnector(Connector):
    platform = "local"
    capabilities = Capabilities(
        official_status_api=True, official_media_api=True, chat=False, chat_official=False,
        notes=["Archivo local procesado con el mismo pipeline que un directo (cadencia real opcional)."],
    )

    @classmethod
    def parse_url(cls, url: str) -> ParsedSource | None:
        raw = url.strip()
        if raw.startswith("file://"):
            raw = urlparse(raw).path
        p = Path(raw).expanduser()
        if p.suffix.lower() in VIDEO_EXT and p.exists() and p.is_file():
            return ParsedSource("local", str(p.resolve()), "file", display_name=p.stem, url=str(p.resolve()))
        return None

    async def check_live(self) -> LiveStatus:
        p = Path(self.source.handle)
        if not p.exists():
            return LiveStatus(False, "file", error="file not found")
        try:
            info = await run_blocking(probe, str(p))
        except ValueError as exc:
            return LiveStatus(False, "file", error=str(exc))
        dur = float(info.get("format", {}).get("duration", 0) or 0)
        return LiveStatus(True, "file", title=p.stem, display_name=p.stem, extra={"duration": dur})

    async def open_capture(self, out_dir: Path) -> CaptureSource:
        return FFmpegSegmenterCapture(
            out_dir, self.source.handle, realtime=bool(self.config.get("realtime", True)),
            start_offset=float(self.config.get("start_offset", 0.0) or 0.0),
        )


class VodConnector(Connector):
    """Recordings / VODs reachable by URL (YouTube videos, Twitch VODs, Kick VODs, direct media URLs)."""

    platform = "vod"
    capabilities = Capabilities(
        official_status_api=False, official_media_api=False, chat=False, chat_official=False,
        notes=["Grabaciones y VOD: mismo pipeline; requiere permiso del creador."],
    )

    @classmethod
    def parse_url(cls, url: str) -> ParsedSource | None:
        u = urlparse(url.strip())
        if u.scheme in ("http", "https") and u.netloc:
            return ParsedSource("vod", url.strip(), "vod", url=url.strip())
        return None

    async def check_live(self) -> LiveStatus:
        url = self.source.url
        if Path(urlparse(url).path).suffix.lower() in VIDEO_EXT | {".m3u8"}:
            return LiveStatus(True, "direct", title=Path(urlparse(url).path).stem)
        try:
            info = await run_blocking(ytdlp_info, url)
        except ResolveError as exc:
            return LiveStatus(False, "yt-dlp", error=str(exc)[:300])
        return LiveStatus(True, "yt-dlp", title=info.get("title"), display_name=info.get("uploader"),
                          extra={"duration": info.get("duration")})

    async def open_capture(self, out_dir: Path) -> CaptureSource:
        url = self.source.url
        realtime = bool(self.config.get("realtime", False))
        if urlparse(url).path.endswith(".m3u8"):
            async def resolver() -> str:
                return url
            return HLSCapture(out_dir, resolver)
        if Path(urlparse(url).path).suffix.lower() in VIDEO_EXT:
            return FFmpegSegmenterCapture(out_dir, url, realtime=realtime)
        # VOD/recording on a platform (YouTube, Twitch VOD, Kick VOD, ...): download to a local file
        # first, then segment it. Handing a raw DASH URL to FFmpeg yields video-only (no audio) or 403s,
        # unlike a live HLS playlist — which is why lives produced clips but VODs did not.
        cid = self.config.get("channel_id")
        cookies = getattr(self.ctx.settings, "youtube_cookies_file", None)
        max_h = int(self.config.get("max_height", 1080) or 1080)
        dest = out_dir / "_source.mp4"

        def _progress(pct: float, phase: str) -> None:
            if cid is not None:
                bus.publish("stream.state", channel_id=cid, state="downloading", pct=round(pct, 1), phase=phase)

        local = await run_blocking(ytdlp_download, url, str(dest), max_h, cookies, _progress)
        # if the download is not an MPEG-TS-copyable codec (e.g. VP9/AV1/Opus), re-encode while segmenting
        reencode = False
        try:
            info = await run_blocking(probe, local)
            streams = info.get("streams", [])
            vcodec = next((s.get("codec_name") for s in streams if s.get("codec_type") == "video"), None)
            acodec = next((s.get("codec_name") for s in streams if s.get("codec_type") == "audio"), None)
            reencode = vcodec not in ("h264", "hevc") or (acodec is not None and acodec not in ("aac", "mp3"))
        except Exception:  # noqa: BLE001 — if probing fails, be safe and re-encode
            reencode = True
        return FFmpegSegmenterCapture(out_dir, local, realtime=realtime, reencode=reencode)


class IngestConnector(Connector):
    """The creator pushes their stream (OBS / multistream service) to the clipper over RTMP or SRT."""

    platform = "ingest"
    capabilities = Capabilities(
        official_status_api=True, official_media_api=True, chat=False, chat_official=False,
        notes=["Vía oficial y universal para el canal propio: OBS → rtmp://<host>:<puerto>/live/<clave>."],
    )

    @classmethod
    def parse_url(cls, url: str) -> ParsedSource | None:
        u = urlparse(url.strip())
        if u.scheme in ("rtmp", "srt"):
            return ParsedSource("ingest", url.strip(), "ingest", display_name=f"Ingest {u.port or ''}", url=url.strip())
        return None

    async def check_live(self) -> LiveStatus:
        # The listener is "live" while waiting; the real state is whether segments arrive.
        if not shutil.which("ffmpeg"):
            return LiveStatus(False, "ingest", error="ffmpeg not installed")
        return LiveStatus(True, "ingest", title="Ingest listener", extra={"listening": True})

    async def open_capture(self, out_dir: Path) -> CaptureSource:
        url = self.source.url
        u = urlparse(url)
        listen_url = url.replace(u.hostname or "", "0.0.0.0", 1) if u.hostname in ("localhost", "127.0.0.1") else url
        if u.scheme == "srt" and "mode=" not in url:
            listen_url += ("&" if "?" in listen_url else "?") + "mode=listener"
        return FFmpegSegmenterCapture(out_dir, listen_url, realtime=False, listen=True)
