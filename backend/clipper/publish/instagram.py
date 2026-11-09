"""Publish a clip to Instagram (Reels or Feed) via the Instagram Graph API.

Requires an Instagram **Professional/Creator** account linked to a Facebook Page, a Meta app with the
`instagram_content_publish` permission (App Review for production), and — this is the key constraint —
the video must be downloadable from a **public HTTPS URL**, because Instagram pulls the media itself.
A local-only install has no public URL, so this adapter is only ready when the server is reachable
from the internet (`CLIPPER_PUBLIC_BASE_URL`).
"""

from __future__ import annotations

import asyncio

import httpx

from clipper.publish.base import NotConnectedError, PublishCapabilities, Publisher, PublishRequest, PublishResult

GRAPH = "https://graph.facebook.com/v21.0"


def caps(platform: str) -> PublishCapabilities:
    label = "Instagram Reels" if platform == "reels" else "Instagram (feed)"
    return PublishCapabilities(
        platform=platform, label=label, network="instagram", official_api=True, needs_public_url=True,
        accepts_local_file=False, privacy_options=["public"], default_privacy="public", requires_review_for_public=True,
        notes=[
            "Requiere cuenta de Instagram Profesional/Creador vinculada a una página de Facebook.",
            "Instagram descarga el vídeo desde una URL pública: la app debe ser accesible desde Internet (CLIPPER_PUBLIC_BASE_URL).",
            "La app de Meta necesita el permiso instagram_content_publish aprobado para publicar en producción.",
        ],
    )


class InstagramPublisher(Publisher):
    def __init__(self, ctx, platform: str = "reels") -> None:
        super().__init__(ctx)
        self.platform = platform
        self.capabilities = caps(platform)

    async def _missing(self) -> list[str]:
        missing = []
        s = self.ctx.settings
        if not (s.meta_app_id and s.meta_app_secret):
            missing.append("meta_app")
        if not (await self.ctx.credentials.get("meta", "ig_user_id") and await self.ctx.credentials.get("meta", "page_token")):
            missing.append("account")
        return missing

    async def publish(self, req: PublishRequest) -> PublishResult:
        if not req.media_url:
            return PublishResult.failure("Instagram necesita el vídeo en una URL pública; configura CLIPPER_PUBLIC_BASE_URL y publica desde un servidor accesible.")
        ig_id = await self.ctx.credentials.get("meta", "ig_user_id")
        token = await self.ctx.credentials.get("meta", "page_token")
        if not (ig_id and token):
            raise NotConnectedError("Conecta tu cuenta de Instagram en Configuración.")
        params = {"media_type": "REELS", "video_url": req.media_url, "caption": req.caption,
                  "share_to_feed": "true", "access_token": token}
        async with httpx.AsyncClient(timeout=60) as c:
            create = await c.post(f"{GRAPH}/{ig_id}/media", data=params)
            js = create.json()
            if create.status_code != 200 or "id" not in js:
                return PublishResult.failure(f"Instagram rechazó el contenedor: {js.get('error', {}).get('message') or create.text[:300]}")
            container = js["id"]
            # Instagram ingests the video asynchronously; wait until the container is FINISHED.
            for _ in range(40):
                await asyncio.sleep(3)
                st = (await c.get(f"{GRAPH}/{container}", params={"fields": "status_code,status", "access_token": token})).json()
                code = st.get("status_code")
                if code == "FINISHED":
                    break
                if code == "ERROR":
                    return PublishResult.failure(f"Instagram no pudo procesar el vídeo: {st.get('status')}")
            else:
                return PublishResult.failure("Instagram tardó demasiado en procesar el vídeo.")
            pub = await c.post(f"{GRAPH}/{ig_id}/media_publish", data={"creation_id": container, "access_token": token})
            pjs = pub.json()
            if pub.status_code != 200 or "id" not in pjs:
                return PublishResult.failure(f"Instagram no publicó: {pjs.get('error', {}).get('message') or pub.text[:300]}")
            media_id = pjs["id"]
            perma = (await c.get(f"{GRAPH}/{media_id}", params={"fields": "permalink", "access_token": token})).json().get("permalink")
        return PublishResult(ok=True, status="published", external_id=media_id, url=perma, detail="Publicado en Instagram.")
