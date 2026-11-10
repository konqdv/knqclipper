"""Tests for web chat -> danmaku parsing (multi-platform fork)."""

import json

from stream_clipper.danmaku.web_chat import (
    collect_web_comments,
    parse_youtube_live_chat_jsonl,
)


def _line(offset_ms, runs, author, paid=False):
    renderer = "liveChatPaidMessageRenderer" if paid else "liveChatTextMessageRenderer"
    return json.dumps({
        "replayChatItemAction": {
            "actions": [{"addChatItemAction": {"item": {
                renderer: {"message": {"runs": runs}, "authorName": {"simpleText": author}}
            }}}],
            "videoOffsetTimeMsec": str(offset_ms),
        }
    })


def test_parse_youtube_live_chat(tmp_path):
    p = tmp_path / "x.live_chat.json"
    p.write_text("\n".join([
        _line(5000, [{"text": "hello "}, {"text": "world"}], "Alice"),
        _line(12000, [{"text": "great stream"}], "Bob", paid=True),
        _line(3000, [{"emoji": {"shortcuts": [":smile:"]}}], "Cara"),
        json.dumps({"replayChatItemAction": {"actions": [{"addLiveChatTickerItemAction": {"item": {}}}],
                                             "videoOffsetTimeMsec": "4000"}}),  # non-text -> skipped
        "not json",  # garbage -> skipped
    ]), encoding="utf-8")

    out = parse_youtube_live_chat_jsonl(p)
    assert [(c.time_offset, c.text, c.user_id) for c in out] == [
        (5.0, "hello world", "Alice"),
        (12.0, "great stream", "Bob"),
        (3.0, ":smile:", "Cara"),
    ]


def test_parse_missing_file(tmp_path):
    assert parse_youtube_live_chat_jsonl(tmp_path / "nope.json") == []


def test_dispatcher_unknown_platform_returns_empty(tmp_path):
    # Kick / generic have no chat collector yet -> [] (pipeline still runs on audio/speech)
    assert collect_web_comments("https://kick.com/foo", tmp_path, platform="kick") == []
    assert collect_web_comments("", tmp_path) == []
