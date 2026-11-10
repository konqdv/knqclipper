"""Fetch time-stamped live chat for web sources (YouTube / Twitch) -> DanmakuComment.

Chat density is the strongest highlight signal in this pipeline. Upstream only had it for
Bilibili/Douyin; this populates IngestResult.comments for YouTube and Twitch too, so the
existing danmaku-based scoring works on them.

Everything here is BEST-EFFORT: on any failure it returns [] and the pipeline keeps working
with audio/speech signals. Added for the multi-platform fork.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

from .models import DanmakuComment


# --------------------------------------------------------------------------- YouTube

def _runs_to_text(runs) -> str:
    parts: list[str] = []
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        if "text" in run:
            parts.append(str(run["text"]))
        elif "emoji" in run:
            shortcuts = (run.get("emoji") or {}).get("shortcuts") or []
            parts.append(str(shortcuts[0]) if shortcuts and shortcuts[0] else "")
    return "".join(parts)


def parse_youtube_live_chat_jsonl(path) -> List[DanmakuComment]:
    """Parse the ``*.live_chat.json`` (JSONL) that yt-dlp writes for a YouTube replay."""
    out: List[DanmakuComment] = []
    p = Path(path)
    if not p.exists():
        return out
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            action = obj.get("replayChatItemAction")
            if not isinstance(action, dict):
                continue
            try:
                offset = float(action.get("videoOffsetTimeMsec")) / 1000.0
            except (TypeError, ValueError):
                continue
            for a in action.get("actions", []) or []:
                item = ((a or {}).get("addChatItemAction") or {}).get("item") or {}
                renderer = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
                if not isinstance(renderer, dict):
                    continue
                text = _runs_to_text((renderer.get("message") or {}).get("runs"))
                if not text.strip():
                    continue
                author = (renderer.get("authorName") or {}).get("simpleText") or ""
                out.append(DanmakuComment(time_offset=max(0.0, offset), text=text.strip(), user_id=str(author)))
    return out


def collect_youtube_chat(url: str, dest_dir, proxy: Optional[str] = None,
                         cookiefile: Optional[str] = None) -> List[DanmakuComment]:
    """Download the YouTube live-chat replay with yt-dlp and parse it. Best-effort."""
    try:
        import yt_dlp
    except Exception:
        return []
    dest = Path(dest_dir)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except Exception:
        return []
    opts = {
        "skip_download": True, "writesubtitles": True, "subtitleslangs": ["live_chat"],
        "outtmpl": str(dest / "webchat.%(ext)s"), "quiet": True, "no_warnings": True, "ignoreerrors": True,
    }
    if proxy:
        opts["proxy"] = proxy
    if cookiefile:
        opts["cookiefile"] = cookiefile
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception:
        pass
    for f in sorted(dest.glob("*.live_chat.json")):
        comments = parse_youtube_live_chat_jsonl(f)
        if comments:
            return comments
    return []


# --------------------------------------------------------------------------- Twitch

_TWITCH_GQL = "https://gql.twitch.tv/gql"
_TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # public web client id (same the twitch.tv site uses)
# persisted-query hash for VideoCommentsByOffsetOrCursor; if Twitch rotates it this simply
# returns no comments (best-effort) and the pipeline falls back to audio/speech signals.
_TWITCH_QUERY_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"


def _twitch_video_id(url: str) -> Optional[str]:
    m = re.search(r"twitch\.tv/videos/(\d+)", url) or re.search(r"/video/(\d+)", url)
    return m.group(1) if m else None


def collect_twitch_vod_chat(url: str, max_pages: int = 4000) -> List[DanmakuComment]:
    """Fetch a Twitch VOD's chat via the public GQL endpoint (paginated). Best-effort."""
    vid = _twitch_video_id(url)
    if not vid:
        return []
    try:
        import httpx
    except Exception:
        return []
    out: List[DanmakuComment] = []
    headers = {"Client-ID": _TWITCH_CLIENT_ID, "Content-Type": "application/json"}
    cursor: Optional[str] = None
    try:
        with httpx.Client(timeout=15.0) as client:
            for _ in range(max_pages):
                variables = {"videoID": vid}
                if cursor:
                    variables["cursor"] = cursor
                else:
                    variables["contentOffsetSeconds"] = 0
                payload = [{
                    "operationName": "VideoCommentsByOffsetOrCursor",
                    "variables": variables,
                    "extensions": {"persistedQuery": {"version": 1, "sha256Hash": _TWITCH_QUERY_HASH}},
                }]
                resp = client.post(_TWITCH_GQL, headers=headers, json=payload)
                if resp.status_code != 200:
                    break
                conn = (((resp.json()[0] or {}).get("data") or {}).get("video") or {}).get("comments") or {}
                edges = conn.get("edges") or []
                for e in edges:
                    node = (e or {}).get("node") or {}
                    off = node.get("contentOffsetSeconds")
                    frags = (node.get("message") or {}).get("fragments") or []
                    text = "".join(str((fr or {}).get("text") or "") for fr in frags)
                    name = (node.get("commenter") or {}).get("displayName") or ""
                    if off is not None and text.strip():
                        out.append(DanmakuComment(time_offset=max(0.0, float(off)), text=text.strip(), user_id=str(name)))
                if not (conn.get("pageInfo") or {}).get("hasNextPage"):
                    break
                cursor = edges[-1].get("cursor") if edges else None
                if not cursor:
                    break
    except Exception:
        return out
    return out


# --------------------------------------------------------------------------- dispatcher

def collect_web_comments(url: str, dest_dir, platform: Optional[str] = None,
                         proxy: Optional[str] = None, cookiefile: Optional[str] = None) -> List[DanmakuComment]:
    """Best-effort chat fetch for a web source. Never raises; [] if unavailable."""
    try:
        u = (url or "").lower()
        plat = (platform or "").lower()
        if plat == "youtube" or "youtube.com" in u or "youtu.be" in u:
            return collect_youtube_chat(url, dest_dir, proxy=proxy, cookiefile=cookiefile)
        if plat == "twitch" or "twitch.tv" in u:
            return collect_twitch_vod_chat(url)
        # Kick and generic sites: no chat yet -> pipeline runs on audio/speech signals.
        return []
    except Exception:
        return []
