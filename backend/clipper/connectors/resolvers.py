"""Playlist resolution helpers backed by streamlink (BSD-2) and yt-dlp (Unlicense).

Only the *playlist URL* is obtained here; segment download is done by ``capture.hls``.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


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
