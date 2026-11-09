"""Publish a clip as a Facebook Reel on a Page (Graph API resumable upload).

Requires a Facebook **Page** (not a personal profile), a Page access token with `pages_manage_posts`,
and a Meta app reviewed for production. Unlike Instagram, Facebook accepts a resumable upload of the
local file, so no public URL is needed.
"""

from __future__ import annotations

import httpx

from clipper.publish.base import NotConnectedError, PublishCapabilities, Publisher, PublishRequest, PublishResult

GRAPH = "https://graph.facebook.com/v21.0"
RUPLOAD = "https://rupload.facebook.com/video-upload/v21.0"

CAPS = PublishCapabilities(
    platform="fb_reels", label="Facebook Reels", network="facebook", official_api=True, needs_public_url=False,
    accepts_local_file=True, privacy_options=["public"], default_privacy="public", requires_review_for_public=True,
    notes=[
        "Publica en una página de Facebook (no en el perfil personal).",
        "La app de Meta necesita pages_manage_posts aprobado para publicar en producción.",
    ],
)


class FacebookPublisher(Publisher):
    platform = "fb_reels"
    capabilities = CAPS

    async def _missing(self) -> list[str]:
        missing = []
        s = self.ctx.settings
        if not (s.meta_app_id and s.meta_app_secret):
            missing.append("meta_app")
        if not (await self.ctx.credentials.get("meta", "page_id") and await self.ctx.credentials.get("meta", "page_token")):
            missing.append("account")
        return missing

    async def publish(self, req: PublishRequest) -> PublishResult:
        page_id = await self.ctx.credentials.get("meta", "page_id")
        token = await self.ctx.credentials.get("meta", "page_token")
        if not (page_id and token):
            raise NotConnectedError("Conecta tu página de Facebook en Configuración.")
        data = req.media_path.read_bytes()
        async with httpx.AsyncClient(timeout=None) as c:
            start = await c.post(f"{GRAPH}/{page_id}/video_reels", data={"upload_phase": "start", "access_token": token})
            sjs = start.json()
            if start.status_code != 200 or "video_id" not in sjs:
                return PublishResult.failure(f"Facebook rechazó el inicio: {sjs.get('error', {}).get('message') or start.text[:300]}")
            video_id = sjs["video_id"]
            up = await c.post(f"{RUPLOAD}/{video_id}", headers={
                "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data)), "Content-Type": "application/octet-stream",
            }, content=data)
            if up.status_code != 200 or not up.json().get("success", True):
                return PublishResult.failure(f"Error subiendo a Facebook ({up.status_code}): {up.text[:200]}")
            finish = await c.post(f"{GRAPH}/{page_id}/video_reels", data={
                "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED", "description": req.caption, "access_token": token})
            fjs = finish.json()
            if finish.status_code != 200 or not fjs.get("success", False):
                return PublishResult.failure(f"Facebook no publicó: {fjs.get('error', {}).get('message') or finish.text[:300]}")
        return PublishResult(ok=True, status="published", external_id=video_id,
                             url=f"https://www.facebook.com/reel/{video_id}", detail="Publicado en Facebook Reels.")
