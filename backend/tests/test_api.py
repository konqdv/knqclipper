"""API smoke test through the real FastAPI app (lifespan, DB, job queue, routers)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from clipper.core.config import override_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("CLIPPER_TWITCH_CLIENT_ID", "CLIPPER_YOUTUBE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    override_settings(data_dir=tmp_path / "data", whisper_model="tiny", live_poll_interval_s=1.0)
    from clipper.main import create_app

    with TestClient(create_app()) as c:
        yield c


def test_api_end_to_end_without_network(client, tts_video):
    st = client.get("/api/system/status").json()
    assert st["counts"]["channels"] == 0 and "cpu_percent" in st["resources"]

    caps = client.get("/api/platforms").json()["capabilities"]
    assert set(caps) >= {"youtube", "twitch", "kick", "local", "vod", "ingest"}
    assert caps["kick"]["chat_official"] and not caps["youtube"]["official_media_api"]

    r = client.post("/api/channels/resolve", json={"url": str(tts_video)}).json()
    assert r["source"]["platform"] == "local" and r["status"]["is_live"]
    assert client.post("/api/channels/resolve", json={"url": "not a url"}).status_code == 400
    # third-party platforms require an explicit permission confirmation
    assert client.post("/api/channels", json={"url": "https://www.twitch.tv/someone", "start": False}).status_code == 400

    ch = client.post("/api/channels", json={"url": str(tts_video), "start": False, "config": {"mode": "autopilot"}}).json()
    assert ch["platform"] == "local" and ch["config"]["mode"] == "autopilot" and not ch["monitoring"]
    patched = client.patch(f"/api/channels/{ch['id']}", json={"config": {"sensitivity": 0.8}}).json()
    assert patched["config"]["sensitivity"] == 0.8 and patched["config"]["mode"] == "autopilot"

    # settings: partial updates merge, unknown sections are rejected
    s = client.put("/api/settings", json={"section": "ranker", "value": {"weights": {"hook": 30}}}).json()
    assert s["ranker"]["weights"]["hook"] == 30 and s["ranker"]["weights"]["emotion"] == 15
    assert client.put("/api/settings", json={"section": "nope", "value": {}}).status_code == 422

    # credentials are write-only
    assert client.put("/api/credentials", json={"provider": "twitch", "name": "client_secret", "value": "s3cr3t"}).status_code == 200
    listing = client.get("/api/credentials").json()
    row = next(c for c in listing if c["provider"] == "twitch" and c["name"] == "client_secret")
    assert row["configured"] and "s3cr3t" not in str(listing)

    # learning refuses to train without enough labelled data
    assert client.post("/api/learning/train", json={}).json()["status"] == "insufficient_data"
    assert client.get("/api/social/insights").json()["publications"] == 0
    assert client.get("/api/moments").json()["total"] == 0
    assert client.get("/api/clips").json()["total"] == 0

    # browser upload -> usable local source (needed when the server is remote)
    up = client.post("/api/uploads", files={"file": ("mi clip.mp4", tts_video.read_bytes(), "video/mp4")}).json()
    assert up["path"].endswith("mi clip.mp4") and up["size"] > 0
    assert client.post("/api/channels/resolve", json={"url": up["path"]}).json()["status"]["is_live"]
    assert client.post("/api/uploads", files={"file": ("notes.txt", b"hi", "text/plain")}).status_code == 422

    # kick webhooks reject unsigned deliveries
    assert client.post("/api/webhooks/kick", content=b"{}").status_code == 400

    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["type"] == "hello"

    assert client.delete(f"/api/channels/{ch['id']}").status_code == 204
    assert client.get("/api/channels").json() == []
    time.sleep(0.1)


def test_spa_route_cannot_escape_frontend_dist(client):
    # encoded slashes reach the catch-all route decoded as "../.." segments
    for path in ("/..%2F..%2Fbackend%2Fpyproject.toml", "/%2E%2E/%2E%2E/backend/pyproject.toml"):
        r = client.get(path)
        assert "universal-ai-live-clipper" not in r.text, f"{path} leaked a file outside frontend/dist"


def test_missing_ui_shows_help_page(tmp_path, monkeypatch):
    import clipper.main as main

    override_settings(data_dir=tmp_path / "data", whisper_model="tiny")
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path / "no-dist")
    with TestClient(main.create_app()) as c:
        r = c.get("/")
        assert r.status_code == 503 and "npm run build" in r.text
        assert c.get("/api/system/status").status_code == 200
