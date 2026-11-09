"""Publishing adapters (via mocked HTTP), readiness, and the social-copy generator.

Live posting to the real platforms cannot be tested without their apps/accounts; these tests verify
the request construction and control flow with an in-memory httpx transport, plus the pure logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from clipper.publish.base import PublishContext, PublishRequest, build_caption
from clipper.publish.copy import generate_copy, heuristic_copy, keywords
from clipper.publish.registry import targets


class FakeCreds:
    def __init__(self, **kv: str) -> None:
        self._d = dict(kv)

    async def get(self, provider: str, name: str):
        return self._d.get(f"{provider}/{name}")

    async def set(self, provider: str, name: str, value: str, expires_at=None):
        self._d[f"{provider}/{name}"] = value

    async def delete(self, provider: str, name: str | None = None):
        self._d.pop(f"{provider}/{name}", None)


def settings(**kw):
    base = dict(google_client_id=None, google_client_secret=None, tiktok_client_key=None, tiktok_client_secret=None,
                meta_app_id=None, meta_app_secret=None, public_base_url=None)
    base.update(kw)
    return SimpleNamespace(**base)


def mock_httpx(monkeypatch, module, handler):
    real = module.httpx.AsyncClient  # capture before patching to avoid recursion

    def factory(*a, **kw):
        kw.pop("timeout", None)
        kw.pop("transport", None)
        return real(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


def test_build_caption_limits_and_hashtags():
    assert build_caption("Título", "Mi texto", ["viral", "#clip"], 100) == "Mi texto\n\n#viral #clip"
    assert build_caption("t", "x" * 200, None, 20).endswith("…") and len(build_caption("t", "x" * 200, None, 20)) == 20


def test_keywords_skips_stopwords():
    ks = keywords("El chef amaba el pan quemado del restaurante restaurante restaurante")
    assert "restaurante" == ks[0] and "el" not in ks and "del" not in ks


async def test_targets_report_missing_requirements():
    ts = {t["platform"]: t for t in await targets(settings(), FakeCreds())}
    assert ts["shorts"]["missing"] == ["google_oauth_app", "account"] and not ts["shorts"]["ready"]
    assert "public_url" in ts["reels"]["missing"]  # instagram needs a public URL
    # fully configured YouTube becomes ready
    ready = {t["platform"]: t for t in await targets(
        settings(google_client_id="x", google_client_secret="y"), FakeCreds(**{"google/refresh_token": "r"}))}
    assert ready["shorts"]["ready"]


async def test_tiktok_uploads_to_drafts(monkeypatch, tmp_path):
    from clipper.publish import tiktok

    video = tmp_path / "c.mp4"
    video.write_bytes(b"VIDEODATA")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/token/"):
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 7200})
        if request.url.path.endswith("/inbox/video/init/"):
            seen["init"] = json.loads(request.content)
            return httpx.Response(200, json={"data": {"publish_id": "pub_1", "upload_url": "https://up.tiktok/put"}, "error": {"code": "ok"}})
        if str(request.url).startswith("https://up.tiktok/put"):
            seen["range"] = request.headers.get("content-range")
            seen["body"] = request.content
            return httpx.Response(200)
        return httpx.Response(404)

    mock_httpx(monkeypatch, tiktok, handler)
    ctx = PublishContext(settings=settings(tiktok_client_key="k", tiktok_client_secret="s"),
                         credentials=FakeCreds(**{"tiktok/refresh_token": "r"}), public_base_url=None)
    res = await tiktok.TikTokPublisher(ctx).publish(PublishRequest(video, None, "hola #fyp", "t", "draft"))
    assert res.ok and res.status == "draft" and res.external_id == "pub_1"
    assert seen["init"]["source_info"]["source"] == "FILE_UPLOAD" and seen["init"]["source_info"]["video_size"] == 9
    assert seen["range"] == "bytes 0-8/9" and seen["body"] == b"VIDEODATA"


async def test_instagram_needs_public_url_then_publishes(monkeypatch, tmp_path):
    from clipper.publish import instagram

    ctx = PublishContext(settings=settings(meta_app_id="a", meta_app_secret="b"),
                         credentials=FakeCreds(**{"meta/ig_user_id": "17", "meta/page_token": "PT"}), public_base_url="https://x.com")
    pub = instagram.InstagramPublisher(ctx, "reels")
    # no public URL -> clear failure, no HTTP
    nores = await pub.publish(PublishRequest(tmp_path / "c.mp4", None, "cap", "t", "public"))
    assert not nores.ok and "URL" in nores.detail

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/17/media"):
            return httpx.Response(200, json={"id": "container1"})
        if request.url.path.endswith("/container1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if request.url.path.endswith("/17/media_publish"):
            return httpx.Response(200, json={"id": "media99"})
        if request.url.path.endswith("/media99"):
            return httpx.Response(200, json={"permalink": "https://instagram.com/reel/abc"})
        return httpx.Response(404)

    mock_httpx(monkeypatch, instagram, handler)
    res = await pub.publish(PublishRequest(tmp_path / "c.mp4", "https://x.com/media/clips/c.mp4", "cap", "t", "public"))
    assert res.ok and res.status == "published" and res.url == "https://instagram.com/reel/abc"
    assert any(p.endswith("/17/media_publish") for p in calls)


def test_social_copy_heuristic_shape():
    c = heuristic_copy(transcript="El chef amaba el pan quemado y me ofreció trabajo esa noche.", title="El cocinero accidental",
                       hook="Te cuento algo", category="podcast", channel="KonqPod", niche="podcast", country="RD", lang="es")
    assert len(c.titles) == 5 and sum(t["recommended"] for t in c.titles) == 1
    assert set(c.descriptions) >= {"facebook", "tiktok", "instagram", "x", "shorts"}
    assert all(len(d) <= 270 for d in c.descriptions["x"])  # X length cap
    assert c.hashtags["instagram"] and all(h.startswith("#") for h in c.hashtags["instagram"])
    assert c.source == "heuristic"


async def test_social_copy_llm_sanitized():
    class FakeLLM:
        vision = False

        async def complete_json(self, system, user, images=None):
            return {"titles": [{"text": "Un título", "recommended": True, "reason": "ok"}],
                    "descriptions": {"x": ["corto " + "y" * 400]},  # too long -> trimmed
                    "hashtags": {"instagram": ["#Viral!!", "bad tag", "#Viral!!"]},  # cleaned + deduped
                    "recommendations": {"best_platform": "tiktok"}}

    c = await generate_copy(FakeLLM(), transcript="texto real del clip", title="t", hook=None, category="gaming",
                            channel=None, niche=None, country=None, lang="es")
    assert c.source == "llm"
    assert len(c.descriptions["x"][0]) <= 270
    assert c.hashtags["instagram"] == ["#viral", "#badtag"]  # normalised, deduped, kept order
    assert c.titles[0]["recommended"]
