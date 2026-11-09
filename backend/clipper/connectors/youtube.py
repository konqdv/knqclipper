"""YouTube Live adapter.

* Live status: YouTube Data API v3 when ``CLIPPER_YOUTUBE_API_KEY`` is set (``videos.list`` 1 unit,
  ``search.list`` 100 units for channel lookups); otherwise yt-dlp on the ``/live`` URL.
* Media: there is no official media API for third-party streams. The HLS manifest is resolved
  with streamlink (fallback yt-dlp). Use only with your own streams or with the creator's permission.
* Chat: ``liveChatMessages.list`` (official; 5 quota units per poll).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from clipper.capture.base import CaptureSource
from clipper.capture.hls import HLSCapture
from clipper.connectors.base import (
    Capabilities, ChatCallback, ChatClient, ChatMessage, Connector, LiveStatus, ParsedSource, run_blocking,
)
from clipper.connectors.resolvers import ResolveError, streamlink_hls_url, ytdlp_hls_url, ytdlp_info

log = logging.getLogger(__name__)
API = "https://www.googleapis.com/youtube/v3"
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _iso(v: str | None) -> float | None:
    if not v:
        return None
    return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()


class YouTubeChat(ChatClient):
    official = True

    def __init__(self, api_key: str, live_chat_id: str) -> None:
        super().__init__()
        self.api_key = api_key
        self.live_chat_id = live_chat_id

    async def run(self, on_message: ChatCallback) -> None:
        page: str | None = None
        first = True
        async with httpx.AsyncClient(timeout=20) as c:
            while not self._stopped:
                params = {"liveChatId": self.live_chat_id, "part": "snippet,authorDetails", "key": self.api_key,
                          "maxResults": 2000}
                if page:
                    params["pageToken"] = page
                try:
                    r = await c.get(f"{API}/liveChat/messages", params=params)
                    r.raise_for_status()
                    js = r.json()
                    self.connected = True
                    self.error = None
                except httpx.HTTPError as exc:
                    self.connected = False
                    self.error = str(exc)
                    log.warning("youtube chat poll failed: %s", exc)
                    await asyncio.sleep(15)
                    continue
                page = js.get("nextPageToken")
                if not first:  # the first page contains backlog; skip it to stay live
                    for it in js.get("items", []):
                        sn = it.get("snippet", {})
                        text = sn.get("displayMessage") or ""
                        await self._emit(on_message, ChatMessage(
                            ts=_iso(sn.get("publishedAt")) or 0.0, user=it.get("authorDetails", {}).get("displayName", ""),
                            text=text, platform="youtube", id=it.get("id"),
                            emotes=re.findall(r":[a-z0-9_-]+:", text),
                        ))
                first = False
                await asyncio.sleep(max(2.0, js.get("pollingIntervalMillis", 5000) / 1000))


class YouTubeConnector(Connector):
    platform = "youtube"
    capabilities = Capabilities(
        official_status_api=True, official_media_api=False, chat=True, chat_official=True,
        notes=[
            "Estado oficial con CLIPPER_YOUTUBE_API_KEY (cuota diaria limitada); sin clave se usa yt-dlp.",
            "YouTube no ofrece API de medios para directos de terceros: el HLS se resuelve con streamlink/yt-dlp.",
            "Para el canal propio, la vía recomendada es la ingesta RTMP desde OBS.",
            "El chat oficial consume 5 unidades de cuota por consulta.",
        ],
    )

    @classmethod
    def parse_url(cls, url: str) -> ParsedSource | None:
        u = urlparse(url.strip() if "://" in url else f"https://{url.strip()}")
        host = (u.netloc or "").lower().removeprefix("www.").removeprefix("m.")
        if host == "youtu.be":
            vid = u.path.strip("/")[:11]
            return ParsedSource("youtube", vid, "video", external_id=vid, url=f"https://www.youtube.com/watch?v={vid}") if _VIDEO_ID.match(vid) else None
        if host not in ("youtube.com", "music.youtube.com"):
            return None
        parts = [p for p in u.path.split("/") if p]
        if u.path == "/watch":
            vid = parse_qs(u.query).get("v", [""])[0]
            if _VIDEO_ID.match(vid):
                return ParsedSource("youtube", vid, "video", external_id=vid, url=f"https://www.youtube.com/watch?v={vid}")
            return None
        if len(parts) >= 2 and parts[0] in ("live", "shorts") and _VIDEO_ID.match(parts[1]):
            return ParsedSource("youtube", parts[1], "video", external_id=parts[1], url=f"https://www.youtube.com/watch?v={parts[1]}")
        if parts and parts[0].startswith("@"):
            return ParsedSource("youtube", parts[0], "channel", url=f"https://www.youtube.com/{parts[0]}")
        if len(parts) >= 2 and parts[0] == "channel":
            return ParsedSource("youtube", parts[1], "channel", external_id=parts[1], url=f"https://www.youtube.com/channel/{parts[1]}")
        if len(parts) >= 2 and parts[0] in ("c", "user"):
            return ParsedSource("youtube", parts[1], "channel", url=f"https://www.youtube.com/{parts[0]}/{parts[1]}")
        return None

    def _live_url(self) -> str:
        if self.source.kind == "video":
            return self.source.url
        return self.source.url.rstrip("/") + "/live"

    async def _api(self, path: str, params: dict) -> dict:
        params = {**params, "key": self.ctx.settings.youtube_api_key}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{API}/{path}", params=params)
            r.raise_for_status()
            return r.json()

    async def _channel_id(self) -> str | None:
        if self.source.external_id and self.source.kind == "channel":
            return self.source.external_id
        if self.source.handle.startswith("@"):
            js = await self._api("channels", {"part": "id,snippet", "forHandle": self.source.handle})
            items = js.get("items", [])
            if items:
                self.source.external_id = items[0]["id"]
                return items[0]["id"]
        return None

    async def _status_from_video(self, vid: str) -> LiveStatus:
        js = await self._api("videos", {"part": "snippet,liveStreamingDetails", "id": vid})
        items = js.get("items", [])
        if not items:
            return LiveStatus(False, "youtube_api", error="video not found")
        it = items[0]
        sn, lsd = it.get("snippet", {}), it.get("liveStreamingDetails", {})
        lbc = sn.get("liveBroadcastContent")
        live = lbc == "live" and not lsd.get("actualEndTime")
        upcoming = lbc == "upcoming"
        is_vod = self.source.kind == "video" and not live and not upcoming  # normal/ended video -> process once
        return LiveStatus(
            live, "youtube_api", title=sn.get("title"), category=sn.get("categoryId"),
            started_at=_iso(lsd.get("actualStartTime")), stream_id=vid,
            viewer_count=int(lsd["concurrentViewers"]) if lsd.get("concurrentViewers") else None,
            thumbnail_url=(sn.get("thumbnails", {}).get("high") or {}).get("url"), display_name=sn.get("channelTitle"),
            extra={"live_chat_id": lsd.get("activeLiveChatId"), "is_upcoming": upcoming, "is_vod": is_vod},
        )

    async def check_live(self) -> LiveStatus:
        if self.ctx.settings.youtube_api_key:
            try:
                if self.source.kind == "video":
                    return await self._status_from_video(self.source.handle)
                cid = await self._channel_id()
                if not cid:
                    return LiveStatus(False, "youtube_api", error="channel not found")
                js = await self._api("search", {"part": "id", "channelId": cid, "eventType": "live", "type": "video", "maxResults": 1})
                items = js.get("items", [])
                if not items:
                    return LiveStatus(False, "youtube_api")
                return await self._status_from_video(items[0]["id"]["videoId"])
            except httpx.HTTPError as exc:
                return LiveStatus(False, "youtube_api", error=f"YouTube API: {exc}")
        try:
            info = await run_blocking(ytdlp_info, self._live_url(), self.ctx.settings.youtube_cookies_file)
        except ResolveError as exc:
            msg = str(exc)
            low = msg.lower()
            if "not a bot" in low or "sign in" in low:
                return LiveStatus(False, "yt-dlp", error=(
                    "YouTube pide verificación anti-bot para esta IP: configura CLIPPER_YOUTUBE_API_KEY "
                    "(estado oficial) o CLIPPER_YOUTUBE_COOKIES_FILE."))
            offline = any(k in low for k in ("not currently live", "will begin", "is offline", "does not have a live"))
            return LiveStatus(False, "yt-dlp", error=None if offline else msg[:300])
        ls = info.get("live_status")
        live = bool(info.get("is_live")) or ls == "is_live"
        upcoming = ls == "is_upcoming"
        is_vod = self.source.kind == "video" and not live and not upcoming
        return LiveStatus(
            live, "yt-dlp", title=info.get("title"), category=(info.get("categories") or [None])[0],
            started_at=info.get("release_timestamp"), stream_id=info.get("id"), viewer_count=info.get("concurrent_view_count"),
            thumbnail_url=info.get("thumbnail"), display_name=info.get("channel") or info.get("uploader"),
            extra={"is_upcoming": upcoming, "is_vod": is_vod},
        )

    async def open_capture(self, out_dir: Path) -> CaptureSource:
        url = self._live_url()
        max_h = int(self.config.get("max_height", 1080))

        async def resolver() -> str:
            try:
                return await run_blocking(streamlink_hls_url, url)
            except ResolveError as exc:
                log.info("streamlink failed for youtube (%s); trying yt-dlp", exc)
                return await run_blocking(ytdlp_hls_url, url, max_h, self.ctx.settings.youtube_cookies_file)

        return HLSCapture(out_dir, resolver, max_height=max_h)

    def chat_client(self, status: LiveStatus) -> ChatClient | None:
        key = self.ctx.settings.youtube_api_key
        chat_id = status.extra.get("live_chat_id") if status.extra else None
        if key and chat_id:
            return YouTubeChat(key, chat_id)
        return None
