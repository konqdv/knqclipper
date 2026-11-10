"""Auto-detect ingest source_type + platform from a pasted URL or local path.

Added for the multi-platform fork (Twitch / Kick / YouTube — live + VOD + clips).
Returns a ``source_type`` the worker already understands:
    local · bili_vod · bili_live · web_vod · web_live

``web_vod`` / ``web_live`` are the generic yt-dlp paths, which already support
YouTube, Twitch and Kick. ``is_live`` is a best-effort hint from the URL shape;
for ambiguous cases (e.g. a plain youtube watch URL) it is ``None`` and the
worker should confirm with yt-dlp's ``live_status`` before choosing vod vs live.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import urlparse

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".ts", ".flv", ".m4v", ".avi", ".wmv", ".m2ts", ".mpg", ".mpeg"}
_DOMAIN_RE = re.compile(r"^[\w-]+(\.[\w-]+)+(/|$|:)", re.IGNORECASE)


def _host_and_path(url: str) -> tuple[str, str]:
    parsed = urlparse(url if "://" in url else "https://" + url)
    host = (parsed.hostname or "").lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host, parsed.path or ""


def _looks_like_local_path(s: str) -> bool:
    if s.lower().startswith("file://"):
        return True
    if "://" in s:
        return False
    if re.match(r"^[a-zA-Z]:[\\/]", s):   # windows drive path, e.g. C:\...
        return True
    if s.startswith(("/", "./", "../", "~")):  # posix path
        return True
    suffix = PureWindowsPath(s).suffix.lower() or PurePosixPath(s).suffix.lower()
    if _DOMAIN_RE.match(s):
        # "clip.mp4" matches the domain shape but is really a bare media filename;
        # "twitch.tv/foo" or "example.com/x" is a URL without scheme.
        return bool(suffix in VIDEO_EXTS and "/" not in s and "\\" not in s)
    return bool("/" in s or "\\" in s or suffix in VIDEO_EXTS)


def detect_source(raw: str) -> dict:
    """Return {source_type, platform, is_live, url} for a pasted URL or path."""
    s = (raw or "").strip()
    out = {"source_type": None, "platform": None, "is_live": None, "url": s}
    if not s:
        return out

    if s.lower().startswith("file://"):
        return {"source_type": "local", "platform": "local", "is_live": False, "url": urlparse(s).path}
    if _looks_like_local_path(s):
        return {"source_type": "local", "platform": "local", "is_live": False, "url": s}

    host, path = _host_and_path(s)
    low = path.lower()
    url = s if "://" in s else "https://" + s
    seg = [p for p in low.split("/") if p]

    def R(stype: str, platform: str, is_live):  # noqa: N802 - tiny helper
        return {"source_type": stype, "platform": platform, "is_live": is_live, "url": url}

    # Bilibili (kept from upstream behaviour)
    if host.endswith("bilibili.com") or host == "b23.tv":
        if host.startswith("live.") or "/live" in low:
            return R("bili_live", "bilibili", True)
        return R("bili_vod", "bilibili", False)

    # Twitch: twitch.tv/<name> = live · /videos/<id> or /<name>/video = VOD · clips = VOD
    if host.endswith("twitch.tv"):
        if host.startswith("clips.") or "/clip/" in low or "/clips" in low:
            return R("web_vod", "twitch", False)
        if low.startswith("/videos/") or re.search(r"/video/\d+", low) or "/videos" in low:
            return R("web_vod", "twitch", False)
        if len(seg) == 1:  # bare channel -> live
            return R("web_live", "twitch", True)
        return R("web_vod", "twitch", None)

    # Kick: kick.com/<name> = live · /<name>/videos/<id> or /video/<id> = VOD · clips = VOD
    if host.endswith("kick.com"):
        if "/clip" in low or "clip" in (seg[-1] if seg else ""):
            return R("web_vod", "kick", False)
        if "/videos/" in low or re.search(r"/video/", low):
            return R("web_vod", "kick", False)
        if len(seg) == 1:  # bare channel -> live
            return R("web_live", "kick", True)
        return R("web_vod", "kick", None)

    # YouTube: /live or /@channel/live = live · watch/shorts/youtu.be = VOD (worker confirms via live_status)
    if host.endswith("youtube.com") or host == "youtu.be":
        if low.rstrip("/").endswith("/live") or "/live/" in low:
            return R("web_live", "youtube", True)
        return R("web_vod", "youtube", None)

    # any other site that yt-dlp might support
    if host:
        return R("web_vod", "web", None)
    return {"source_type": "local", "platform": "local", "is_live": False, "url": s}
