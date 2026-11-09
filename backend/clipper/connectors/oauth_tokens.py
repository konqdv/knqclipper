"""App-access-token cache for client-credentials OAuth flows (Twitch, Kick)."""

from __future__ import annotations

import asyncio
import time

import httpx

_cache: dict[str, tuple[str, float]] = {}
_lock = asyncio.Lock()


async def client_credentials_token(token_url: str, client_id: str, client_secret: str, scope: str | None = None) -> str:
    key = f"{token_url}|{client_id}"
    async with _lock:
        cached = _cache.get(key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        data = {"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"}
        if scope:
            data["scope"] = scope
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(token_url, data=data)
            r.raise_for_status()
            js = r.json()
        token = js["access_token"]
        _cache[key] = (token, time.time() + float(js.get("expires_in", 3600)))
        return token


def invalidate(token_url: str, client_id: str) -> None:
    _cache.pop(f"{token_url}|{client_id}", None)
