"""Tests for URL auto-detection (multi-platform fork)."""

from stream_clipper.ingest.detect import detect_source


def _st(url):
    d = detect_source(url)
    return (d["platform"], d["source_type"], d["is_live"])


def test_twitch_live_vod_clip():
    assert _st("https://twitch.tv/somestreamer") == ("twitch", "web_live", True)
    assert _st("twitch.tv/somestreamer") == ("twitch", "web_live", True)
    assert _st("https://www.twitch.tv/videos/123456789") == ("twitch", "web_vod", False)
    assert _st("https://www.twitch.tv/somestreamer/clip/FunnyClipSlug") == ("twitch", "web_vod", False)
    assert _st("https://clips.twitch.tv/FunnyClipSlug") == ("twitch", "web_vod", False)


def test_kick_live_vod_clip():
    assert _st("https://kick.com/somestreamer") == ("kick", "web_live", True)
    assert _st("kick.com/somestreamer") == ("kick", "web_live", True)
    assert _st("https://kick.com/somestreamer/videos/abc-123") == ("kick", "web_vod", False)
    assert _st("https://kick.com/video/abc-123") == ("kick", "web_vod", False)
    assert _st("https://kick.com/somestreamer/clips/clip_xyz") == ("kick", "web_vod", False)


def test_youtube_video_live_shorts():
    # plain watch URL -> VOD by default, is_live unknown (worker confirms)
    assert _st("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == ("youtube", "web_vod", None)
    assert _st("https://youtu.be/dQw4w9WgXcQ") == ("youtube", "web_vod", None)
    assert _st("https://www.youtube.com/shorts/abcdefghijk") == ("youtube", "web_vod", None)
    # explicit live URLs
    assert _st("https://www.youtube.com/@SomeChannel/live") == ("youtube", "web_live", True)
    assert _st("https://www.youtube.com/live/abcdefghijk") == ("youtube", "web_live", True)


def test_bilibili_preserved():
    assert _st("https://www.bilibili.com/video/BV1xx411c7mD")[1] == "bili_vod"
    assert _st("https://live.bilibili.com/123456")[1] == "bili_live"


def test_local_paths():
    assert _st("C:\\Users\\me\\Videos\\clip.mp4")[1] == "local"
    assert _st("/home/me/videos/clip.mkv")[1] == "local"
    assert _st("file:///home/me/clip.mp4")[1] == "local"
    assert _st("myvideo.mp4")[1] == "local"


def test_generic_site_falls_back_to_web_vod():
    assert _st("https://www.douyin.com/video/123")[0] in ("web", "douyin") or _st("https://some.site/video")[1] == "web_vod"
    assert _st("https://example.com/some/video")[1] == "web_vod"


def test_empty():
    d = detect_source("")
    assert d["source_type"] is None
