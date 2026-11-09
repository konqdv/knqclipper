"""Publish a clip to YouTube as a Short (Data API v3 resumable upload, OAuth).

This is the most self-contained path: it reuses the Google account the user connects for YouTube
Analytics (the connect flow requests the upload scope too) and uploads the local file directly.
Note: uploads from an unverified Google app are forced to `private` by Google until the app passes
OAuth verification; the user can flip the video to public in YouTube Studio, or verify their app.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from clipper.publish.base import NotConnectedError, PublishCapabilities, Publisher, PublishRequest, PublishResult
from clipper.social.youtube_analytics import refresh_access_token

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
PRIVACY = {"public": "public", "private": "private", "unlisted": "unlisted"}

CAPS = PublishCapabilities(
    platform="shorts", label="YouTube Shorts", network="youtube", official_api=True, needs_public_url=False,
    accepts_local_file=True, privacy_options=["public", "unlisted", "private"], default_privacy="private",
    requires_review_for_public=False,
    notes=[
        "Sube directamente a tu canal con la cuenta de Google que conectas.",
        "Vertical y <= 3 min se publica como Short.",
        "Hasta que verifiques tu app de Google, YouTube fuerza los vídeos subidos por API a 'privado'; cámbialo en YouTube Studio.",
    ],
)


class YouTubePublisher(Publisher):
    platform = "shorts"
    capabilities = CAPS

    async def _token(self) -> str:
        s = self.ctx.settings
        refresh = await self.ctx.credentials.get("google", "refresh_token")
        if not (s.google_client_id and s.google_client_secret and refresh):
            raise NotConnectedError("Conecta tu cuenta de Google en Configuración.")
        tok = await refresh_access_token(s.google_client_id, s.google_client_secret, refresh)
        return tok["access_token"]

    async def _missing(self) -> list[str]:
        s = self.ctx.settings
        missing = []
        if not (s.google_client_id and s.google_client_secret):
            missing.append("google_oauth_app")
        if not await self.ctx.credentials.get("google", "refresh_token"):
            missing.append("account")
        return missing

    async def publish(self, req: PublishRequest) -> PublishResult:
        token = await self._token()
        path = Path(req.media_path)
        size = path.stat().st_size
        desc = req.caption if "#short" in req.caption.lower() else (req.caption + "\n\n#Shorts").strip()
        meta = {"snippet": {"title": (req.title or "Clip")[:100], "description": desc[:5000], "categoryId": "24"},
                "status": {"privacyStatus": PRIVACY.get(req.privacy, "private"), "selfDeclaredMadeForKids": False}}
        async with httpx.AsyncClient(timeout=None) as c:
            start = await c.post(UPLOAD_URL, headers={
                "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/*", "X-Upload-Content-Length": str(size),
            }, content=json.dumps(meta))
            if start.status_code not in (200, 201) or "location" not in start.headers:
                return PublishResult.failure(f"YouTube rechazó la subida ({start.status_code}): {start.text[:300]}")
            location = start.headers["location"]
            with path.open("rb") as fh:
                up = await c.put(location, headers={"Content-Type": "video/*", "Content-Length": str(size)}, content=fh.read())
            if up.status_code not in (200, 201):
                return PublishResult.failure(f"Error subiendo a YouTube ({up.status_code}): {up.text[:300]}")
            vid = up.json().get("id")
        priv = PRIVACY.get(req.privacy, "private")
        return PublishResult(ok=True, status="published", external_id=vid,
                             url=f"https://youtube.com/shorts/{vid}" if vid else None,
                             detail="Subido como privado (verifica tu app de Google para publicar en público)." if priv == "private" else "Publicado.",
                             log={"privacy": priv})
