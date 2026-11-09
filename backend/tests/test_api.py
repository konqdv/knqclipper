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

    # kick webhooks reject unsigned deliveries
    assert client.post("/api/webhooks/kick", content=b"{}").status_code == 400

    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["type"] == "hello"

    assert client.delete(f"/api/channels/{ch['id']}").status_code == 204
    assert client.get("/api/channels").json() == []
    time.sleep(0.1)
