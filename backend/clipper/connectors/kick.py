"""Kick adapter.

* Live status: official public API ``GET https://api.kick.com/public/v1/channels?slug=`` with an
  app access token (``client_credentials`` on ``id.kick.com``). Without credentials the
  (unofficial) web endpoint ``kick.com/api/v2/channels/<slug>/livestream`` is used and flagged.
* Media: AWS IVS HLS playlist resolved by streamlink (relies on Kick's internal web API).
* Chat (official): ``chat.message.sent`` webhooks delivered to ``/api/webhooks/kick`` and verified
  with RSA-SHA256 against Kick's public key. Requires ``CLIPPER_PUBLIC_BASE_URL``.
* Chat (unofficial, opt-in ``CLIPPER_KICK_UNOFFICIAL_CHAT=true``): Pusher WebSocket.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from clipper.capture.base import CaptureSource
from clipper.capture.hls import HLSCapture
from clipper.connectors import oauth_tokens
from clipper.connectors.base import (
    Capabilities, ChatCallback, ChatClient, ChatMessage, Connector, LiveStatus, ParsedSource, run_blocking,
)
from clipper.connectors.resolvers import streamlink_hls_url

log = logging.getLogger(__name__)
API = "https://api.kick.com/public/v1"
TOKEN_URL = "https://id.kick.com/oauth/token"
WEB_API = "https://kick.com/api/v2"
PUSHER_URL = "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0&flash=false"
_URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?kick\.com/(?P<slug>[A-Za-z0-9_-]{2,64})/?(?:\?.*)?$")
_RESERVED = {"categories", "following", "browse", "search", "dashboard", "video", "terms-of-service"}
_EMOTE_RE = re.compile(r"\[emote:(\d+):([^\]]+)\]")


def _iso(v: str | None) -> float | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# --- webhooks -------------------------------------------------------------------------
_public_key_pem: bytes | None = None


async def kick_public_key() -> bytes:
    global _public_key_pem
    if _public_key_pem is None:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{API}/public-key")
            r.raise_for_status()
            _public_key_pem = r.json()["data"]["public_key"].encode()
    return _public_key_pem


def verify_webhook_signature(public_key_pem: bytes, message_id: str, timestamp: str, body: bytes, signature_b64: str) -> bool:
    key = serialization.load_pem_public_key(public_key_pem)
    signed = f"{message_id}.{timestamp}.".encode() + body
    try:
        key.verify(base64.b64decode(signature_b64), signed, padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr]
        return True
    except Exception:
        return False


class KickWebhookChat(ChatClient):
    """Receives chat messages pushed by the webhook endpoint (official path)."""

    official = True
    _registry: dict[str, "KickWebhookChat"] = {}

    def __init__(self, broadcaster_user_id: str) -> None:
        super().__init__()
        self.broadcaster_user_id = str(broadcaster_user_id)
        self._queue: asyncio.Queue[ChatMessage] = asyncio.Queue(maxsize=5000)

    @classmethod
    def deliver(cls, payload: dict) -> bool:
        bid = str((payload.get("broadcaster") or {}).get("user_id", ""))
        client = cls._registry.get(bid)
        if client is None:
            return False
        content = payload.get("content", "")
        msg = ChatMessage(
            ts=_iso(payload.get("created_at")) or time.time(), user=(payload.get("sender") or {}).get("username", ""),
            text=_EMOTE_RE.sub(lambda m: m.group(2), content), platform="kick", id=payload.get("message_id"),
            emotes=[m.group(2) for m in _EMOTE_RE.finditer(content)],
        )
        try:
            client._queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass
        return True

    async def run(self, on_message: ChatCallback) -> None:
        self._registry[self.broadcaster_user_id] = self
        self.connected = True
        try:
            while not self._stopped:
                try:
                    msg = await asyncio.wait_for(self._queue.get(), 1.0)
                except asyncio.TimeoutError:
                    continue
                await self._emit(on_message, msg)
        finally:
            self._registry.pop(self.broadcaster_user_id, None)
            self.connected = False


class KickPusherChat(ChatClient):
    """Unofficial read-only chat via Kick's Pusher WebSocket (opt-in)."""

    official = False

    def __init__(self, chatroom_id: int) -> None:
        super().__init__()
        self.chatroom_id = chatroom_id

    async def run(self, on_message: ChatCallback) -> None:
        backoff = 1.0
        while not self._stopped:
            try:
                async with websockets.connect(PUSHER_URL, open_timeout=15) as ws:
                    await ws.send(json.dumps({"event": "pusher:subscribe",
                                              "data": {"auth": "", "channel": f"chatrooms.{self.chatroom_id}.v2"}}))
                    self.connected, self.error, backoff = True, None, 1.0
                    async for raw in ws:
                        if self._stopped:
                            return
                        ev = json.loads(raw)
                        if ev.get("event") == "pusher:ping":
                            await ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                            continue
                        if ev.get("event") != "App\\Events\\ChatMessageEvent":
                            continue
                        d = json.loads(ev.get("data") or "{}")
                        content = d.get("content", "")
                        await self._emit(on_message, ChatMessage(
                            ts=_iso(d.get("created_at")) or time.time(), user=(d.get("sender") or {}).get("username", ""),
                            text=_EMOTE_RE.sub(lambda m: m.group(2), content), platform="kick", id=d.get("id"),
                            emotes=[m.group(2) for m in _EMOTE_RE.finditer(content)],
                        ))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.connected, self.error = False, str(exc)
                log.warning("kick pusher chat disconnected: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)


async def subscribe_chat_webhook(token: str, broadcaster_user_id: int) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{API}/events/subscriptions", headers={"Authorization": f"Bearer {token}"},
                         json={"broadcaster_user_id": broadcaster_user_id, "method": "webhook",
                               "events": [{"name": "chat.message.sent", "version": 1}]})
        r.raise_for_status()
        return r.json()


class KickConnector(Connector):
    platform = "kick"
    capabilities = Capabilities(
        official_status_api=True, official_media_api=False, chat=True, chat_official=True,
        needs_credentials_for_status=True,
        notes=[
            "Estado oficial con CLIPPER_KICK_CLIENT_ID/SECRET (token de app). Sin credenciales se usa el endpoint web no oficial.",
            "El vídeo (AWS IVS) se resuelve con streamlink usando la API interna de kick.com, que puede cambiar.",
            "Chat oficial por webhooks: requiere CLIPPER_PUBLIC_BASE_URL accesible desde Internet.",
            "Chat por WebSocket Pusher solo si CLIPPER_KICK_UNOFFICIAL_CHAT=true (no oficial).",
        ],
    )

    @classmethod
    def parse_url(cls, url: str) -> ParsedSource | None:
        m = _URL_RE.match(url.strip())
        if not m or m.group("slug").lower() in _RESERVED:
            return None
        slug = m.group("slug").lower()
        return ParsedSource("kick", slug, "channel", url=f"https://kick.com/{slug}")

    async def _token(self) -> str:
        s = self.ctx.settings
        return await oauth_tokens.client_credentials_token(TOKEN_URL, s.kick_client_id, s.kick_client_secret)

    async def check_live(self) -> LiveStatus:
        s = self.ctx.settings
        slug = self.source.handle
        if s.kick_client_id and s.kick_client_secret:
            try:
                token = await self._token()
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.get(f"{API}/channels", params={"slug": slug}, headers={"Authorization": f"Bearer {token}"})
                    r.raise_for_status()
                    data = r.json().get("data") or []
                if not data:
                    return LiveStatus(False, "kick_api", error="channel not found")
                ch = data[0]
                stream = ch.get("stream") or {}
                self.source.external_id = str(ch.get("broadcaster_user_id") or self.source.external_id or "")
                return LiveStatus(
                    bool(stream.get("is_live")), "kick_api", title=ch.get("stream_title"),
                    category=(ch.get("category") or {}).get("name"), started_at=_iso(stream.get("start_time")),
                    viewer_count=stream.get("viewer_count"), thumbnail_url=stream.get("thumbnail"),
                    extra={"broadcaster_user_id": ch.get("broadcaster_user_id")},
                )
            except httpx.HTTPError as exc:
                return LiveStatus(False, "kick_api", error=f"Kick API: {exc}")
        try:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}) as c:
                r = await c.get(f"{WEB_API}/channels/{slug}")
                if r.status_code == 404:
                    return LiveStatus(False, "kick_web_unofficial", error="channel not found")
                r.raise_for_status()
                ch = r.json()
            ls = ch.get("livestream") or {}
            user = ch.get("user") or {}
            cats = ls.get("categories") or []
            return LiveStatus(
                bool(ls.get("is_live")), "kick_web_unofficial", title=ls.get("session_title"),
                category=cats[0].get("name") if cats else None, started_at=_iso(ls.get("created_at")),
                viewer_count=ls.get("viewer_count"), stream_id=str(ls.get("id")) if ls.get("id") else None,
                display_name=user.get("username"), avatar_url=user.get("profile_pic"),
                extra={"chatroom_id": (ch.get("chatroom") or {}).get("id"), "broadcaster_user_id": ch.get("user_id")},
            )
        except (httpx.HTTPError, ValueError) as exc:
            return LiveStatus(False, "kick_web_unofficial", error=f"kick.com: {exc}")

    async def open_capture(self, out_dir: Path) -> CaptureSource:
        url = self.source.url

        async def resolver() -> str:
            return await run_blocking(streamlink_hls_url, url)

        return HLSCapture(out_dir, resolver, max_height=int(self.config.get("max_height", 1080)))

    def chat_client(self, status: LiveStatus) -> ChatClient | None:
        s = self.ctx.settings
        bid = (status.extra or {}).get("broadcaster_user_id")
        if s.public_base_url and bid and s.kick_client_id:
            return KickWebhookChat(str(bid))
        chatroom = (status.extra or {}).get("chatroom_id")
        if s.kick_unofficial_chat and chatroom:
            return KickPusherChat(int(chatroom))
        return None
