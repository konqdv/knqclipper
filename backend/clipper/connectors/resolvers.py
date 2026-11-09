"""Playlist resolution helpers backed by streamlink (BSD-2) and yt-dlp (Unlicense).

Only the *playlist URL* is obtained here; segment download is done by ``capture.hls``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

_DL_EXT = {".mp4", ".mkv", ".webm", ".m4v", ".mov", ".ts", ".flv"}


class ResolveError(Exception):
    pass


def streamlink_hls_url(url: str, options: dict[str, Any] | None = None, quality: str = "best") -> str:
    import streamlink
    from streamlink.stream.hls import HLSStream

    session = streamlink.Streamlink()
    for k, v in (options or {}).items():
        session.set_option(k, v)
    try:
        streams = session.streams(url)
    except Exception as exc:  # streamlink raises PluginError / NoPluginError
        raise ResolveError(f"streamlink: {exc}") from exc
    if not streams:
        raise ResolveError("streamlink: no playable streams (offline or blocked)")
    stream = streams.get(quality) or streams.get("best") or next(iter(streams.values()))
    if not isinstance(stream, HLSStream):
        raise ResolveError(f"streamlink returned a non-HLS stream ({type(stream).__name__})")
    mv = getattr(stream, "multivariant", None)
    if mv is not None and getattr(mv, "uri", None):
        return mv.uri  # let our capture pick the variant (keeps max-height policy)
    return stream.url


def streamlink_is_live(url: str) -> bool:
    try:
        streamlink_hls_url(url)
        return True
    except ResolveError:
        return False


class _QuietLogger:
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)


def ytdlp_info(url: str, cookies_file: str | None = None) -> dict[str, Any]:
    import yt_dlp

    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
                            "logger": _QuietLogger()}
    if cookies_file:
        opts["cookiefile"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            return ydl.extract_info(url, download=False) or {}
        except Exception as exc:
            raise ResolveError(f"yt-dlp: {exc}") from exc


def ytdlp_hls_url(url: str, max_height: int = 1080, cookies_file: str | None = None) -> str:
    info = ytdlp_info(url, cookies_file)
    if info.get("manifest_url"):
        return info["manifest_url"]
    fmts = [f for f in info.get("formats", []) if str(f.get("protocol", "")).startswith("m3u8")
            and f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")]
    fmts = [f for f in fmts if (f.get("height") or 0) <= max_height] or fmts
    if not fmts:
        raise ResolveError("yt-dlp: no muxed HLS formats")
    best = max(fmts, key=lambda f: ((f.get("height") or 0), f.get("tbr") or 0))
    return best["url"]


def ytdlp_download(
    url: str,
    out_path: str,
    max_height: int = 1080,
    cookies_file: str | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> str:
    """Download a VOD/recording (merged audio+video) to a local file and return its path.

    Prefers H.264 + AAC so the file can be stream-copied into MPEG-TS segments without re-encoding.
    This is the robust path for YouTube/Twitch/Kick VODs, whose media is DASH (video-only URLs) and
    cannot be handed to FFmpeg as a single muxed URL the way a live HLS playlist can.
    """
    import yt_dlp

    stem = Path(out_path).with_suffix("")
    stem.parent.mkdir(parents=True, exist_ok=True)
    for old in stem.parent.glob(stem.name + ".*"):  # clear partials from a previous attempt
        try:
            old.unlink()
        except OSError:
            pass
    last = {"t": 0.0}

    def _hook(d: dict[str, Any]) -> None:
        if progress is None:
            return
        st = d.get("status")
        if st == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            now = time.time()
            if now - last["t"] >= 1.0:
                last["t"] = now
                progress((done / total * 100.0) if total else 0.0, "downloading")
        elif st == "finished":
            progress(100.0, "merging")

    opts: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
        "logger": _QuietLogger(), "progress_hooks": [_hook],
        "outtmpl": str(stem) + ".%(ext)s",
        "format": (
            f"bv*[vcodec^=avc1][height<=?{max_height}]+ba[acodec^=mp4a]/"
            f"b[vcodec^=avc1][height<=?{max_height}]/"
            f"bv*[height<=?{max_height}]+ba/b[height<=?{max_height}]/b"
        ),
        "merge_output_format": "mp4",
        "retries": 10, "fragment_retries": 10, "concurrent_fragment_downloads": 4,
        "overwrites": True,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            ydl.download([url])
        except Exception as exc:
            raise ResolveError(f"yt-dlp download: {exc}") from exc
    produced = [p for p in sorted(stem.parent.glob(stem.name + ".*")) if p.suffix.lower() in _DL_EXT]
    if not produced:
        raise ResolveError("yt-dlp download produced no file")
    # prefer a merged mp4 if several parts linger
    produced.sort(key=lambda p: (p.suffix.lower() != ".mp4", -p.stat().st_size))
    return str(produced[0])
