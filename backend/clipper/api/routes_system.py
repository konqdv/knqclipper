"""System status, settings, credentials, OAuth, WebSocket events and platform webhooks."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from clipper import __version__
from clipper.captions.styles import COLOR_SWATCHES, FONT_FAMILIES, PRESETS
from clipper.connectors.kick import KickWebhookChat, kick_public_key, verify_webhook_signature
from clipper.core import system
from clipper.core.appsettings import DEFAULTS, store
from clipper.core.config import get_settings
from clipper.core.credentials import SETTING_MAP, get_credentials
from clipper.core.events import bus
from clipper.core.jobs import get_queue
from clipper.db.models import Channel, Clip, Moment
from clipper.db.session import session_scope
from clipper.llm.providers import make_provider
from clipper.storage.manager import get_storage
from clipper.stream.manager import get_manager

router = APIRouter(tags=["system"])


@router.get("/api/system/status")
async def status() -> dict[str, Any]:
    settings = get_settings()
    mgr = get_manager()
    async with session_scope() as s:
        counts = {
            "channels": (await s.execute(select(func.count(Channel.id)))).scalar(),
            "monitoring": (await s.execute(select(func.count(Channel.id)).where(Channel.monitoring.is_(True)))).scalar(),
            "moments": (await s.execute(select(func.count(Moment.id)))).scalar(),
            "pending_review": (await s.execute(select(func.count(Moment.id)).where(Moment.status == "pending"))).scalar(),
            "clips": (await s.execute(select(func.count(Clip.id)))).scalar(),
            "clips_ready": (await s.execute(select(func.count(Clip.id)).where(Clip.status == "ready"))).scalar(),
            "clips_rendering": (await s.execute(select(func.count(Clip.id)).where(Clip.status.in_(["queued", "rendering"])))).scalar(),
        }
    asr = mgr._asr
    return {
        "version": __version__, "time": time.time(), "resources": system.snapshot(settings.data_dir), "counts": counts,
        "sessions": mgr.snapshot(), "queue": get_queue().pending(), "queue_backend": settings.queue_backend,
        "asr": {"model": settings.whisper_model, "loaded": bool(asr and asr.loaded), "device": asr.device if asr else None,
                "compute_type": asr.compute_type if asr else None, "error": mgr._asr_failed,
                "rtf": round(asr.realtime_factor, 3) if asr and asr.realtime_factor else None},
        "llm": {"provider": settings.llm_provider, "model": settings.llm_model if settings.llm_provider != "none" else None},
        "disk_free_gb": round(get_storage().free_gb(), 2),
    }


@router.get("/api/system/llm")
async def llm_health() -> dict[str, Any]:
    p = get_manager().llm
    return await p.health() if p else {"provider": "none"}


@router.get("/api/settings")
async def get_app_settings() -> dict[str, Any]:
    s = get_settings()
    return {
        "app": store.all(), "defaults": DEFAULTS,
        "captions": {"presets": PRESETS, "fonts": FONT_FAMILIES, "colors": COLOR_SWATCHES},
        "runtime": {"whisper_model": s.whisper_model, "whisper_device": s.whisper_device, "llm_provider": s.llm_provider,
                    "llm_model": s.llm_model, "llm_base_url": s.llm_base_url, "llm_vision": s.llm_vision,
                    "queue_backend": s.queue_backend, "render_concurrency": s.render_concurrency, "max_streams": s.max_streams,
                    "data_dir": str(s.data_dir), "public_base_url": s.public_base_url,
                    "kick_unofficial_chat": s.kick_unofficial_chat, "api_token_required": bool(s.api_token)},
    }


class SettingsIn(BaseModel):
    section: str
    value: dict[str, Any]


@router.put("/api/settings")
async def put_app_settings(body: SettingsIn) -> dict[str, Any]:
    if body.section not in DEFAULTS:
        raise HTTPException(422, f"unknown section {body.section}")
    if body.section == "ranker" and "weights" in body.value:
        w = body.value["weights"]
        if any(float(v) < 0 for v in w.values()):
            raise HTTPException(422, "weights must be >= 0")
    return {body.section: await store.update(body.section, body.value)}


class LLMIn(BaseModel):
    provider: str
    model: str | None = None
    base_url: str | None = None
    vision: bool | None = None


@router.put("/api/settings/llm")
async def put_llm(body: LLMIn) -> dict[str, Any]:
    if body.provider not in ("none", "ollama", "openai", "anthropic"):
        raise HTTPException(422, "invalid provider")
    s = get_settings()
    s.llm_provider = body.provider  # type: ignore[assignment]
    if body.model:
        s.llm_model = body.model
    s.llm_base_url = body.base_url or None
    if body.vision is not None:
        s.llm_vision = body.vision
    await store.update("runtime_llm", {"provider": s.llm_provider, "model": s.llm_model, "base_url": s.llm_base_url, "vision": s.llm_vision})
    mgr = get_manager()
    mgr.llm = make_provider(s)
    for sess in mgr.sessions.values():
        sess.engine.context.llm = mgr.llm
    return {"provider": s.llm_provider, "model": s.llm_model}


# --- credentials -------------------------------------------------------------------------------------
class CredentialIn(BaseModel):
    provider: str
    name: str
    value: str


@router.get("/api/credentials")
async def list_credentials() -> list[dict[str, Any]]:
    return await get_credentials().listing(get_settings())


@router.put("/api/credentials")
async def set_credential(body: CredentialIn) -> dict[str, Any]:
    if (body.provider, body.name) not in SETTING_MAP:
        raise HTTPException(422, "unknown credential")
    creds = get_credentials()
    await creds.set(body.provider, body.name, body.value.strip())
    await creds.apply(get_settings())
    return {"ok": True}


@router.delete("/api/credentials/{provider}/{name}")
async def delete_credential(provider: str, name: str) -> dict[str, Any]:
    await get_credentials().delete(provider, name)
    attr = SETTING_MAP.get((provider, name))
    if attr:
        setattr(get_settings(), attr, None)
    return {"ok": True}


# --- Google OAuth (YouTube Analytics / live chat of the user's own channel) -------------------------------
_oauth_states: dict[str, float] = {}
GOOGLE_SCOPES = ("https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly "
                 "https://www.googleapis.com/auth/youtube.upload")


def _redirect_uri(request: Request, provider: str = "google") -> str:
    base = get_settings().public_base_url or str(request.base_url).rstrip("/")
    return f"{base.rstrip('/')}/api/oauth/{provider}/callback"


@router.get("/api/oauth/google/start")
async def google_start(request: Request):
    s = get_settings()
    if not (s.google_client_id and s.google_client_secret):
        raise HTTPException(400, "Configura google client_id / client_secret primero")
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = time.time()
    q = urlencode({"client_id": s.google_client_id, "redirect_uri": _redirect_uri(request), "response_type": "code",
                   "scope": GOOGLE_SCOPES, "access_type": "offline", "prompt": "consent", "state": state})
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{q}")


@router.get("/api/oauth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code or _oauth_states.pop(state, 0) < time.time() - 600:
        return HTMLResponse("<h3>OAuth cancelado o caducado.</h3>", status_code=400)
    s = get_settings()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret,
            "redirect_uri": _redirect_uri(request), "grant_type": "authorization_code"})
    if r.status_code != 200:
        return HTMLResponse(f"<h3>Error de OAuth: {r.status_code}</h3>", status_code=400)
    tok = r.json()
    if tok.get("refresh_token"):
        await get_credentials().set("google", "refresh_token", tok["refresh_token"])
    return HTMLResponse("<h3>Cuenta de Google conectada. Puedes cerrar esta ventana.</h3>")


# --- TikTok OAuth (publicar como borrador) ----------------------------------------------------------
TIKTOK_SCOPES = "user.info.basic,video.upload"


@router.get("/api/oauth/tiktok/start")
async def tiktok_start(request: Request):
    s = get_settings()
    if not (s.tiktok_client_key and s.tiktok_client_secret):
        raise HTTPException(400, "Configura tu app de TikTok primero")
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = time.time()
    q = urlencode({"client_key": s.tiktok_client_key, "redirect_uri": _redirect_uri(request, "tiktok"),
                   "response_type": "code", "scope": TIKTOK_SCOPES, "state": state})
    return RedirectResponse(f"https://www.tiktok.com/v2/auth/authorize/?{q}")


@router.get("/api/oauth/tiktok/callback")
async def tiktok_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code or _oauth_states.pop(state, 0) < time.time() - 600:
        return HTMLResponse("<h3>OAuth de TikTok cancelado o caducado.</h3>", status_code=400)
    s = get_settings()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://open.tiktokapis.com/v2/oauth/token/", data={
            "client_key": s.tiktok_client_key, "client_secret": s.tiktok_client_secret, "code": code,
            "grant_type": "authorization_code", "redirect_uri": _redirect_uri(request, "tiktok")},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
    js = r.json()
    if r.status_code != 200 or "access_token" not in js:
        return HTMLResponse(f"<h3>Error de OAuth de TikTok: {js.get('error_description') or r.status_code}</h3>", status_code=400)
    creds = get_credentials()
    await creds.set("tiktok", "access_token", js["access_token"])
    await creds.set("tiktok", "refresh_token", js.get("refresh_token", ""))
    await creds.set("tiktok", "open_id", js.get("open_id", ""))
    await creds.set("tiktok", "expires_at", str(time.time() + float(js.get("expires_in", 86400))))
    return HTMLResponse("<h3>Cuenta de TikTok conectada. Puedes cerrar esta ventana.</h3>")


# --- Meta OAuth (Facebook Pages + Instagram) --------------------------------------------------------
META_SCOPES = ("pages_show_list,pages_manage_posts,pages_read_engagement,business_management,"
               "instagram_basic,instagram_content_publish")
META_GRAPH = "https://graph.facebook.com/v21.0"


@router.get("/api/oauth/meta/start")
async def meta_start(request: Request):
    s = get_settings()
    if not (s.meta_app_id and s.meta_app_secret):
        raise HTTPException(400, "Configura tu app de Meta primero")
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = time.time()
    q = urlencode({"client_id": s.meta_app_id, "redirect_uri": _redirect_uri(request, "meta"),
                   "response_type": "code", "scope": META_SCOPES, "state": state})
    return RedirectResponse(f"https://www.facebook.com/v21.0/dialog/oauth?{q}")


@router.get("/api/oauth/meta/callback")
async def meta_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code or _oauth_states.pop(state, 0) < time.time() - 600:
        return HTMLResponse("<h3>OAuth de Meta cancelado o caducado.</h3>", status_code=400)
    s = get_settings()
    redirect = _redirect_uri(request, "meta")
    async with httpx.AsyncClient(timeout=20) as c:
        tok = (await c.get(f"{META_GRAPH}/oauth/access_token", params={
            "client_id": s.meta_app_id, "client_secret": s.meta_app_secret, "redirect_uri": redirect, "code": code})).json()
        if "access_token" not in tok:
            return HTMLResponse(f"<h3>Error de OAuth de Meta: {tok.get('error', {}).get('message')}</h3>", status_code=400)
        longlived = (await c.get(f"{META_GRAPH}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": s.meta_app_id, "client_secret": s.meta_app_secret,
            "fb_exchange_token": tok["access_token"]})).json()
        user_token = longlived.get("access_token", tok["access_token"])
        pages = (await c.get(f"{META_GRAPH}/me/accounts", params={
            "fields": "id,name,access_token,instagram_business_account{id,username}", "access_token": user_token})).json()
    data = pages.get("data") or []
    if not data:
        return HTMLResponse("<h3>No se encontró ninguna página de Facebook en tu cuenta. Crea una página y vincula tu Instagram profesional.</h3>", status_code=400)
    page = next((p for p in data if p.get("instagram_business_account")), data[0])
    creds = get_credentials()
    await creds.set("meta", "user_token", user_token)
    await creds.set("meta", "page_id", str(page["id"]))
    await creds.set("meta", "page_token", page["access_token"])
    await creds.set("meta", "page_name", page.get("name", ""))
    ig = page.get("instagram_business_account")
    if ig:
        await creds.set("meta", "ig_user_id", str(ig["id"]))
        await creds.set("meta", "ig_username", ig.get("username", ""))
    extra = f" · Instagram @{ig['username']}" if ig and ig.get("username") else " (sin Instagram vinculado)"
    return HTMLResponse(f"<h3>Conectado: página «{page.get('name')}»{extra}. Puedes cerrar esta ventana.</h3>")


# --- Kick official webhooks ---------------------------------------------------------------------------
_seen_kick: dict[str, float] = {}


@router.post("/api/webhooks/kick")
async def kick_webhook(request: Request) -> dict[str, Any]:
    body = await request.body()
    h = request.headers
    mid, ts, sig = h.get("Kick-Event-Message-Id", ""), h.get("Kick-Event-Message-Timestamp", ""), h.get("Kick-Event-Signature", "")
    if not (mid and ts and sig):
        raise HTTPException(400, "missing signature headers")
    if not verify_webhook_signature(await kick_public_key(), mid, ts, body, sig):
        raise HTTPException(401, "invalid signature")
    now = time.time()
    if mid in _seen_kick:
        return {"ok": True, "duplicate": True}
    _seen_kick[mid] = now
    for k in [k for k, v in _seen_kick.items() if v < now - 600]:
        _seen_kick.pop(k, None)
    if h.get("Kick-Event-Type") == "chat.message.sent":
        KickWebhookChat.deliver(json.loads(body))
    return {"ok": True}


# --- WebSocket event stream -------------------------------------------------------------------------------
@router.websocket("/api/ws")
async def ws_events(ws: WebSocket) -> None:
    token = get_settings().api_token
    if token and ws.query_params.get("token") != token:
        await ws.close(code=4401)
        return
    await ws.accept()
    q = bus.subscribe()
    try:
        await ws.send_text(json.dumps({"type": "hello", "data": {"version": __version__}, "ts": time.time()}))
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15)
                await ws.send_text(ev.to_json())
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "ping", "data": {}, "ts": time.time()}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)
