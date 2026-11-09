"""Publish a clip to TikTok (Content Posting API).

Without passing TikTok's app audit, an app may upload to the user's **drafts/inbox** (the user taps
"Post" inside the TikTok app). Direct public posting requires the audited `video.publish` scope, so
this adapter uploads to the inbox and reports a `draft` result. The local file is uploaded directly.
"""

from __future__ import annotations

import time

import httpx

from clipper.publish.base import NotConnectedError, PublishCapabilities, Publisher, PublishRequest, PublishResult

API = "https://open.tiktokapis.com/v2"
CAPS = PublishCapabilities(
    platform="tiktok", label="TikTok", network="tiktok", official_api=True, needs_public_url=False,
    accepts_local_file=True, privacy_options=["draft"], default_privacy="draft", requires_review_for_public=True,
    notes=[
        "Sube el clip a tus borradores de TikTok; abres TikTok y pulsas 'Publicar'.",
        "La publicación pública automática necesita que TikTok audite tu app (scope video.publish).",
    ],
)


async def tiktok_access_token(settings, creds) -> str:
    key, secret = settings.tiktok_client_key, settings.tiktok_client_secret
    if not (key and secret):
        raise NotConnectedError("Configura tu app de TikTok en Configuración.")
    access = await creds.get("tiktok", "access_token")
    exp = await creds.get("tiktok", "expires_at")
    if access and exp and float(exp) > time.time() + 60:
        return access
    refresh = await creds.get("tiktok", "refresh_token")
    if not refresh:
        raise NotConnectedError("Conecta tu cuenta de TikTok en Configuración.")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/oauth/token/", data={
            "client_key": key, "client_secret": secret, "grant_type": "refresh_token", "refresh_token": refresh,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
        r.raise_for_status()
        js = r.json()
    if "access_token" not in js:
        raise NotConnectedError(f"TikTok rechazó el token: {js.get('error_description') or js}")
    await creds.set("tiktok", "access_token", js["access_token"])
    await creds.set("tiktok", "expires_at", str(time.time() + float(js.get("expires_in", 86400))))
    if js.get("refresh_token"):
        await creds.set("tiktok", "refresh_token", js["refresh_token"])
    return js["access_token"]


class TikTokPublisher(Publisher):
    platform = "tiktok"
    capabilities = CAPS

    async def _missing(self) -> list[str]:
        s = self.ctx.settings
        missing = []
        if not (s.tiktok_client_key and s.tiktok_client_secret):
            missing.append("tiktok_app")
        if not await self.ctx.credentials.get("tiktok", "refresh_token"):
            missing.append("account")
        return missing

    async def publish(self, req: PublishRequest) -> PublishResult:
        token = await tiktok_access_token(self.ctx.settings, self.ctx.credentials)
        data = req.media_path.read_bytes()
        size = len(data)
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=None) as c:
            init = await c.post(f"{API}/post/publish/inbox/video/init/", headers={**headers, "Content-Type": "application/json; charset=UTF-8"},
                                json={"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}})
            js = init.json()
            if init.status_code != 200 or js.get("error", {}).get("code") not in (None, "ok"):
                return PublishResult.failure(f"TikTok rechazó la subida: {js.get('error', {}).get('message') or init.text[:300]}")
            upload_url = js["data"]["upload_url"]
            publish_id = js["data"]["publish_id"]
            put = await c.put(upload_url, headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size - 1}/{size}", "Content-Length": str(size)}, content=data)
            if put.status_code not in (200, 201):
                return PublishResult.failure(f"Error subiendo a TikTok ({put.status_code}): {put.text[:200]}")
        return PublishResult(ok=True, status="draft", external_id=publish_id,
                             detail="En tus borradores de TikTok. Abre la app y pulsa Publicar.", log={"publish_id": publish_id})
