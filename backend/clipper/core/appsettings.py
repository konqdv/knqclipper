"""Runtime-editable application settings persisted in the ``settings`` table.

Environment variables (``Settings``) configure infrastructure; these settings configure behaviour
that the user tunes from the UI (scoring weights, thresholds, caption defaults, learning).
"""

from __future__ import annotations

import copy
from typing import Any

from clipper.db.models import AppSetting
from clipper.db.session import session_scope

DEFAULTS: dict[str, dict[str, Any]] = {
    "ranker": {
        # maximum points per criterion (sum = 100)
        "weights": {
            "hook": 25, "entertainment": 20, "narrative": 15, "emotion": 15,
            "flow": 10, "shareability": 10, "audio_quality": 5,
        },
        "tiers": {"excellent": 90, "high": 75, "review": 60},
        "context_adaptive": True,
    },
    "detection": {
        "scan_interval_s": 40.0,
        "min_duration": 15.0,
        "max_duration": 90.0,
        "dedup_iou": 0.5,
        "dedup_text_similarity": 0.6,
        "min_candidate_score": 35.0,  # below this a candidate is discarded silently
    },
    "render": {"crf": 18, "preset": "medium", "audio_bitrate": "192k", "fps": 30, "width": 1080, "height": 1920},
    "captions": {
        "style": "dynamic", "font": "Montserrat", "font_size": 78, "primary_color": "#FFFFFF",
        "highlight_color": "#FFE600", "outline_color": "#000000", "outline": 6, "shadow": 3,
        "position": "lower", "margin_v": 420, "uppercase": True, "max_words_per_line": 4,
        "animation": "pop",
    },
    "learning": {
        "min_samples": 20, "blend_k": 40, "auto_promote": True, "promote_margin": 0.01,
        "performance_weight": 0.15,
    },
}

CHANNEL_DEFAULTS: dict[str, Any] = {
    "mode": "review",  # review | autopilot
    "min_score": 75.0,
    "buffer_seconds": 120,
    "pre_seconds": 45.0,
    "post_seconds": 12.0,
    "sensitivity": 0.5,  # 0 (few candidates) .. 1 (many)
    "chat_enabled": True,
    "realtime": True,  # local files: emulate live cadence
    "caption_style": None,  # None = global default
    "reframe_mode": "auto",
    "language": None,
    "min_duration": None,
    "max_duration": None,
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class SettingsStore:
    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}

    async def load(self) -> None:
        from sqlalchemy import select

        async with session_scope() as s:
            rows = (await s.execute(select(AppSetting))).scalars().all()
            stored = {r.key: r.value for r in rows}
        self._cache = {k: deep_merge(v, stored.get(k, {})) for k, v in DEFAULTS.items()}
        self._cache.update({k: v for k, v in stored.items() if k not in DEFAULTS})

    def get(self, key: str) -> dict[str, Any]:
        if key not in self._cache:
            self._cache[key] = copy.deepcopy(DEFAULTS.get(key, {}))
        return self._cache[key]

    def all(self) -> dict[str, dict[str, Any]]:
        return {k: self.get(k) for k in DEFAULTS}

    async def update(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        merged = deep_merge(self.get(key), value)
        async with session_scope() as s:
            row = await s.get(AppSetting, key)
            if row is None:
                s.add(AppSetting(key=key, value=merged))
            else:
                row.value = merged
        self._cache[key] = merged
        return merged


store = SettingsStore()


def channel_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    return deep_merge(CHANNEL_DEFAULTS, raw or {})
