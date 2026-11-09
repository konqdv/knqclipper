"""Twitch adapter.

* Live status: Helix ``GET /streams`` with an app access token (official). Without credentials,
  streamlink resolution is used as a best-effort check.
* Media: HLS playlist from the web player (resolved by streamlink), captured by ``HLSCapture``
  with server-side ad segments filtered out.
* Chat: IRC over WebSocket, anonymous read-only login (``justinfan``), with IRCv3 tags.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime
from pathlib import Path

import httpx
import websockets

from clipper.capture.base import CaptureSource
from clipper.capture.hls import HLSCapture
from clipper.connectors import oauth_tokens
from clipper.connectors.base import (
    Capabilities, ChatCallback, ChatClient, ChatMessage, Connector, LiveStatus, ParsedSource, run_blocking,
)
from clipper.connectors.resolvers import streamlink_hls_url

log = logging.getLogger(__name__)

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX = "https://api.twitch.tv/helix"
IRC_WS = "wss://irc-ws.chat.twitch.tv:443"
GQL = "https://gql.twitch.tv/gql"
WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # public client id of the twitch.tv web player
_URL_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/(?P<login>[A-Za-z0-9_]{2,25})/?(?:\?.*)?$")
_RESERVED = {"videos", "directory", "settings", "p", "downloads", "jobs", "turbo", "search", "subscriptions"}


def parse_irc(line: str) -> dict | None:
    """Parse one IRC line with optional IRCv3 tags."""
    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw_tags, line = line[1:].split(" ", 1)
        for kv in raw_tags.split(";"):
            k, _, v = kv.partition("=")
            tags[k] = v
    prefix = ""
    if line.startswith(":"):
        prefix, line = line[1:].split(" ", 1)
    trailing = None
    if " :" in line:
        line, trailing = line.split(" :", 1)
    parts = line.split()
    if not parts:
        return None
    return {"tags": tags, "prefix": prefix, "command": parts[0], "params": parts[1:], "trailing": trailing}


def emotes_from_tags(text: str, emotes_tag: str) -> list[str]:
    """Twitch ``emotes`` tag: ``id:start-end,start-end/id:start-end`` (code-point offsets)."""
    out: list[str] = []
    if not emotes_tag:
        return out
    chars = list(text)
    for part in emotes_tag.split("/"):
        _, _, ranges = part.partition(":")
        for rng in ranges.split(","):
            a, _, b = rng.partition("-")
            if a.isdigit() and b.isdigit():
                out.append("".join(chars[int(a): int(b) + 1]))
    return out


class TwitchIRCChat(ChatClient):
    official = True

    def __init__(self, login: str) -> None:
        super().__init__()
        self.login = login.lower()

    async def run(self, on_message: ChatCallback) -> None:
        backoff = 1.0
        while not self._stopped:
            try:
                async with websockets.connect(IRC_WS, open_timeout=15, ping_interval=None) as ws:
                    await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                    await ws.send("PASS SCHMOOPIIE")
                    await ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
                    await ws.send(f"JOIN #{self.login}")
                    self.connected = True
                    self.error = None
                    backoff = 1.0
                    async for raw in ws:
                        if self._stopped:
                            return
                        for line in str(raw).split("\r\n"):
                            if not line:
                                continue
                            if line.startswith("PING"):
                                await ws.send("PONG :tmi.twitch.tv")
                                continue
                            msg = parse_irc(line)
                            if not msg:
                                continue
                            if msg["command"] == "RECONNECT":
                                raise ConnectionError("server requested reconnect")
                            if msg["command"] != "PRIVMSG" or msg["trailing"] is None:
                                continue
                            tags = msg["tags"]
                            text = msg["trailing"]
                            ts = int(tags.get("tmi-sent-ts", "0") or 0) / 1000 or datetime.now().timestamp()
                            await self._emit(on_message, ChatMessage(
                                ts=ts, user=tags.get("display-name") or msg["prefix"].split("!")[0], text=text,
                                platform="twitch", emotes=emotes_from_tags(text, tags.get("emotes", "")),
                                id=tags.get("id"), badges=[b.split("/")[0] for b in tags.get("badges", "").split(",") if b],
                            ))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.connected = False
                self.error = str(exc)
                log.warning("twitch chat %s disconnected: %s", self.login, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)


class TwitchConnector(Connector):
    platform = "twitch"
    capabilities = Capabilities(
        official_status_api=True, official_media_api=False, chat=True, chat_official=True,
        notes=[
            "Estado oficial vía Helix con CLIPPER_TWITCH_CLIENT_ID/SECRET; sin credenciales se usa el GraphQL público del reproductor web (no oficial).",
            "El vídeo se obtiene de la playlist HLS del reproductor web (no hay API oficial de medios).",
            "Los anuncios insertados en el stream se descartan y no forman parte de los clips.",
            "Chat de solo lectura por IRC anónimo.",
        ],
    )

    @classmethod
    def parse_url(cls, url: str) -> ParsedSource | None:
        m = _URL_RE.match(url.strip())
        if not m or m.group("login").lower() in _RESERVED:
            return None
        login = m.group("login").lower()
        return ParsedSource("twitch", login, "channel", url=f"https://www.twitch.tv/{login}")

    async def _helix(self, path: str, params: dict) -> dict:
        s = self.ctx.settings
        token = await oauth_tokens.client_credentials_token(TOKEN_URL, s.twitch_client_id, s.twitch_client_secret)
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{HELIX}{path}", params=params,
                            headers={"Client-Id": s.twitch_client_id, "Authorization": f"Bearer {token}"})
            if r.status_code == 401:
                oauth_tokens.invalidate(TOKEN_URL, s.twitch_client_id)
            r.raise_for_status()
            return r.json()

    async def check_live(self) -> LiveStatus:
        s = self.ctx.settings
        login = self.source.handle
        if s.twitch_client_id and s.twitch_client_secret:
            try:
                data = (await self._helix("/streams", {"user_login": login})).get("data", [])
                users = (await self._helix("/users", {"login": login})).get("data", [])
                u = users[0] if users else {}
                if not data:
                    return LiveStatus(False, "helix", display_name=u.get("display_name"), avatar_url=u.get("profile_image_url"))
                d = data[0]
                started = datetime.fromisoformat(d["started_at"].replace("Z", "+00:00")).timestamp()
                return LiveStatus(
                    True, "helix", title=d.get("title"), category=d.get("game_name"), started_at=started,
                    viewer_count=d.get("viewer_count"), stream_id=d.get("id"),
                    thumbnail_url=(d.get("thumbnail_url") or "").replace("{width}", "640").replace("{height}", "360"),
                    display_name=u.get("display_name") or d.get("user_name"), avatar_url=u.get("profile_image_url"),
                )
            except httpx.HTTPError as exc:
                return LiveStatus(False, "helix", error=f"Helix: {exc}")
        return await self._web_status(login)

    async def _web_status(self, login: str) -> LiveStatus:
        """Fallback without Helix credentials: the public web player's GraphQL (unofficial)."""
        query = ('query($login:String!){user(login:$login){displayName profileImageURL(width:150) '
                 'stream{id title type viewersCount createdAt game{name}}}}')
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(GQL, json={"query": query, "variables": {"login": login}},
                                 headers={"Client-Id": WEB_CLIENT_ID})
                r.raise_for_status()
                user = (r.json().get("data") or {}).get("user")
        except (httpx.HTTPError, ValueError) as exc:
            return LiveStatus(False, "twitch_web_unofficial", error=f"twitch.tv: {exc}")
        if user is None:
            return LiveStatus(False, "twitch_web_unofficial", error="channel not found")
        st = user.get("stream")
        base = dict(display_name=user.get("displayName"), avatar_url=user.get("profileImageURL"))
        if not st or st.get("type") != "live":
            return LiveStatus(False, "twitch_web_unofficial", **base)
        started = datetime.fromisoformat(st["createdAt"].replace("Z", "+00:00")).timestamp() if st.get("createdAt") else None
        return LiveStatus(True, "twitch_web_unofficial", title=st.get("title"), category=(st.get("game") or {}).get("name"),
                          started_at=started, viewer_count=st.get("viewersCount"), stream_id=st.get("id"), **base)

    async def open_capture(self, out_dir: Path) -> CaptureSource:
        url = self.source.url

        async def resolver() -> str:
            return await run_blocking(streamlink_hls_url, url)

        return HLSCapture(out_dir, resolver, filter_ads=True, max_height=int(self.config.get("max_height", 1080)))

    def chat_client(self, status: LiveStatus) -> ChatClient | None:
        return TwitchIRCChat(self.source.handle)
