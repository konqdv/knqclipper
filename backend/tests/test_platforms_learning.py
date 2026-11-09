"""URL parsing, chat protocols, webhook signatures and the feedback learning model."""

from __future__ import annotations

import base64

import numpy as np
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from clipper.connectors.kick import KickConnector, verify_webhook_signature
from clipper.connectors.registry import parse_source
from clipper.connectors.twitch import emotes_from_tags, parse_irc
from clipper.connectors.youtube import YouTubeConnector
from clipper.learning.features import build_features
from clipper.learning.model import DurationPreferences, Example, FeedbackModel, cross_validate, fit_model, label_from_feedback


@pytest.mark.parametrize("url,platform,handle,kind", [
    ("https://www.twitch.tv/Ibai", "twitch", "ibai", "channel"),
    ("twitch.tv/some_user", "twitch", "some_user", "channel"),
    ("https://kick.com/xqc", "kick", "xqc", "channel"),
    ("https://www.youtube.com/@LofiGirl", "youtube", "@LofiGirl", "channel"),
    ("https://youtu.be/jfKfPfyJRdk", "youtube", "jfKfPfyJRdk", "video"),
    ("https://www.youtube.com/watch?v=jfKfPfyJRdk&t=1", "youtube", "jfKfPfyJRdk", "video"),
    ("https://www.youtube.com/channel/UCSJ4gkVC6NrvII8umztf0Ow", "youtube", "UCSJ4gkVC6NrvII8umztf0Ow", "channel"),
    ("rtmp://localhost:1935/live/key", "ingest", "rtmp://localhost:1935/live/key", "ingest"),
    ("https://example.com/video.mp4", "vod", "https://example.com/video.mp4", "vod"),
])
def test_url_parsing(url, platform, handle, kind):
    p = parse_source(url)
    assert p is not None and (p.platform, p.handle, p.kind) == (platform, handle, kind)


def test_reserved_paths_are_not_channels():
    assert KickConnector.parse_url("https://kick.com/categories") is None
    assert parse_source("https://www.twitch.tv/directory").platform == "vod"
    assert YouTubeConnector.parse_url("https://www.youtube.com/feed/trending") is None


def test_twitch_irc_parsing_with_tags_and_emotes():
    line = ("@badges=subscriber/12;display-name=Fan;emotes=25:0-4,12-16;id=abc;tmi-sent-ts=1700000000000 "
            ":fan!fan@fan.tmi.twitch.tv PRIVMSG #chan :Kappa hello Kappa")
    msg = parse_irc(line)
    assert msg["command"] == "PRIVMSG" and msg["trailing"] == "Kappa hello Kappa"
    assert emotes_from_tags(msg["trailing"], msg["tags"]["emotes"]) == ["Kappa", "Kappa"]


def test_kick_webhook_signature_verification():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    body = b'{"content":"hola"}'
    sig = base64.b64encode(key.sign(b"msg-1.2026-11-09T10:00:00Z." + body, padding.PKCS1v15(), hashes.SHA256())).decode()
    assert verify_webhook_signature(pem, "msg-1", "2026-11-09T10:00:00Z", body, sig)
    assert not verify_webhook_signature(pem, "msg-1", "2026-11-09T10:00:00Z", body + b" ", sig)


def test_feedback_labels():
    assert label_from_feedback("approve", None, False) == (1, 1.0)
    assert label_from_feedback("reject", None, False)[0] == 0
    assert label_from_feedback("rate", 5, False)[0] == 1 and label_from_feedback("rate", 1, False)[0] == 0
    assert label_from_feedback("rate", None, True)[0] == 0


def _example(rng, creator: str, humor: float, duration: float, label: int) -> Example:
    subs = {"hook": rng.uniform(0.3, 0.8), "entertainment": humor, "narrative": rng.uniform(0, 1), "emotion": rng.uniform(0, 1),
            "flow": rng.uniform(0.4, 1), "shareability": rng.uniform(0, 1), "audio_quality": 0.9}
    f = build_features(subscores=subs, category="podcast", moment_types=["humor"] if humor > 0.5 else ["story"],
                       stats={"duration": duration})
    return Example(f, label, 1.0, creator, "podcast", duration)


def test_model_learns_creator_specific_preferences_and_validates():
    rng = np.random.default_rng(3)
    ex = []
    for _ in range(80):  # creator A likes short humorous clips, creator B likes long stories
        h, d = rng.uniform(0, 1), rng.uniform(15, 80)
        ex.append(_example(rng, "a", h, d, int(h > 0.5 and d < 40)))
        h, d = rng.uniform(0, 1), rng.uniform(15, 80)
        ex.append(_example(rng, "b", h, d, int(h < 0.5 and d > 45)))
    metrics = cross_validate(ex)
    assert metrics["auc"] > 0.8
    params, _ = fit_model(ex)
    m = FeedbackModel(params)
    fa = _example(rng, "a", 0.9, 25, 1).features
    fb = _example(rng, "b", 0.1, 60, 1).features
    assert m.predict_proba(fa, "a", "podcast") > m.predict_proba(fa, "b", "podcast")
    assert m.predict_proba(fb, "b", "podcast") > m.predict_proba(fb, "a", "podcast")
    prefs = DurationPreferences.fit(ex)
    lo_a, hi_a = prefs.range("a", "podcast")
    lo_b, hi_b = prefs.range("b", "podcast")
    assert hi_a < lo_b + 15 and lo_b > lo_a
    mult = m.weight_multipliers("a", "podcast")
    assert all(0.5 <= v <= 1.5 for v in mult.values())


async def test_youtube_video_is_classified_live_vod_or_upcoming(monkeypatch):
    from types import SimpleNamespace

    from clipper.connectors.base import ConnectorContext
    from clipper.connectors.youtube import YouTubeConnector

    ctx = ConnectorContext(settings=SimpleNamespace(youtube_api_key="k", youtube_cookies_file=None))
    conn = YouTubeConnector(YouTubeConnector.parse_url("https://www.youtube.com/watch?v=jfKfPfyJRdk"), ctx)

    async def fake_api(path, params, video):
        return {"items": [{"snippet": {"liveBroadcastContent": video["lbc"], "title": "t", "channelTitle": "c"},
                           "liveStreamingDetails": video["lsd"]}]}

    # a normal uploaded video -> VOD (process once), not a live
    monkeypatch.setattr(conn, "_api", lambda p, q: fake_api(p, q, {"lbc": "none", "lsd": {}}))
    st = await conn.check_live()
    assert not st.is_live and st.extra["is_vod"] and not st.extra["is_upcoming"]

    # an ended live is now a watchable VOD too
    monkeypatch.setattr(conn, "_api", lambda p, q: fake_api(p, q, {"lbc": "none", "lsd": {"actualEndTime": "2026-01-01T00:00:00Z"}}))
    assert (await conn.check_live()).extra["is_vod"]

    # currently live -> not a VOD
    monkeypatch.setattr(conn, "_api", lambda p, q: fake_api(p, q, {"lbc": "live", "lsd": {}}))
    st = await conn.check_live()
    assert st.is_live and not st.extra["is_vod"]

    # scheduled premiere/live -> wait, not a VOD
    monkeypatch.setattr(conn, "_api", lambda p, q: fake_api(p, q, {"lbc": "upcoming", "lsd": {}}))
    st = await conn.check_live()
    assert not st.is_live and st.extra["is_upcoming"] and not st.extra["is_vod"]


async def test_youtube_channel_handle_is_never_vod(monkeypatch):
    from types import SimpleNamespace

    from clipper.connectors.base import ConnectorContext
    from clipper.connectors.youtube import YouTubeConnector

    ctx = ConnectorContext(settings=SimpleNamespace(youtube_api_key="k", youtube_cookies_file=None))
    conn = YouTubeConnector(YouTubeConnector.parse_url("https://www.youtube.com/@LofiGirl"), ctx)

    async def no_live(path, params):
        return {"items": []} if path == "search" else {"items": [{"id": "UC123"}]}

    monkeypatch.setattr(conn, "_api", no_live)
    st = await conn.check_live()
    assert not st.is_live and not st.extra.get("is_vod")  # a channel waits for a live, never one-shot
