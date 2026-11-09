"""Encrypted credential store. Values entered in the UI are encrypted at rest and overlaid on settings.

Environment variables always win over stored credentials; the API never returns secret values.
"""

from __future__ import annotations

import os
import time

from sqlalchemy import delete, select

from clipper.core.secrets import SecretBox
from clipper.db.models import Credential
from clipper.db.session import session_scope

# (provider, name) -> Settings attribute it maps to
SETTING_MAP = {
    ("twitch", "client_id"): "twitch_client_id", ("twitch", "client_secret"): "twitch_client_secret",
    ("kick", "client_id"): "kick_client_id", ("kick", "client_secret"): "kick_client_secret",
    ("youtube", "api_key"): "youtube_api_key", ("google", "client_id"): "google_client_id",
    ("google", "client_secret"): "google_client_secret", ("llm", "api_key"): "llm_api_key",
    ("tiktok", "client_key"): "tiktok_client_key", ("tiktok", "client_secret"): "tiktok_client_secret",
    ("meta", "app_id"): "meta_app_id", ("meta", "app_secret"): "meta_app_secret",
}
SECRET_NAMES = {"client_secret", "api_key", "refresh_token", "access_token", "client_key", "app_secret", "user_token", "page_token"}


class CredentialStore:
    def __init__(self, box: SecretBox) -> None:
        self.box = box

    async def set(self, provider: str, name: str, value: str, expires_at: float | None = None) -> None:
        async with session_scope() as s:
            await s.execute(delete(Credential).where(Credential.provider == provider, Credential.name == name))
            s.add(Credential(provider=provider, name=name, value_enc=self.box.encrypt(value), expires_at=expires_at))

    async def get(self, provider: str, name: str) -> str | None:
        async with session_scope() as s:
            row = (await s.execute(select(Credential).where(Credential.provider == provider, Credential.name == name))).scalars().first()
            return self.box.decrypt(row.value_enc) if row else None

    async def delete(self, provider: str, name: str | None = None) -> None:
        async with session_scope() as s:
            q = delete(Credential).where(Credential.provider == provider)
            if name:
                q = q.where(Credential.name == name)
            await s.execute(q)

    async def listing(self, settings) -> list[dict]:
        async with session_scope() as s:
            rows = (await s.execute(select(Credential))).scalars().all()
        stored = {(r.provider, r.name): r for r in rows}
        out = []
        for (prov, name), attr in SETTING_MAP.items():
            env = f"CLIPPER_{attr.upper()}" in os.environ
            row = stored.get((prov, name))
            out.append({"provider": prov, "name": name, "configured": bool(getattr(settings, attr, None)),
                        "source": "env" if env else ("stored" if row else None),
                        "secret": name in SECRET_NAMES, "updated_at": row.updated_at if row else None})
        for (prov, name), row in stored.items():
            if (prov, name) not in SETTING_MAP:
                out.append({"provider": prov, "name": name, "configured": True, "source": "oauth",
                            "secret": True, "updated_at": row.updated_at, "expires_at": row.expires_at})
        return out

    async def apply(self, settings) -> None:
        """Overlay stored credentials onto settings (without overriding environment variables)."""
        for (prov, name), attr in SETTING_MAP.items():
            if f"CLIPPER_{attr.upper()}" in os.environ:
                continue
            val = await self.get(prov, name)
            if val:
                setattr(settings, attr, val)


_store: CredentialStore | None = None


def get_credentials() -> CredentialStore:
    if _store is None:
        raise RuntimeError("credential store not initialised")
    return _store


def set_credentials(c: CredentialStore) -> None:
    global _store
    _store = c


time  # noqa: B018
