"""Video Editor model: every user-adjustable aspect of a clip, validated and turned into a render plan.

Edits never overwrite the original moment: the moment keeps the detected transcript and boundaries,
the clip keeps the edited version, and the difference is stored with the feedback for learning.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel, Field, field_validator


class EditWord(BaseModel):
    w: str
    s: float
    e: float
    p: float | None = None
    spk: str | None = None
    emphasis: bool | None = None


class ReframeEdit(BaseModel):
    mode: str = "auto"  # auto|track|split|fit|center|manual
    manual: dict[str, float] = Field(default_factory=lambda: {"x": 0.5, "y": 0.5, "zoom": 1.0})
    plan: dict[str, Any] | None = None  # cached plan computed for (mode, manual, trim)
    plan_key: str | None = None

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("auto", "track", "split", "fit", "center", "manual"):
            raise ValueError("invalid reframe mode")
        return v


class TitleEdit(BaseModel):
    text: str = ""
    duration: float = 3.0
    style: dict[str, Any] = Field(default_factory=dict)


class WatermarkEdit(BaseModel):
    text: str = ""
    image: str | None = None
    position: str = "top-right"
    opacity: float = 0.6
    font_size: int = 40


class ClipEdit(BaseModel):
    trim_start: float
    trim_end: float
    words: list[EditWord] = Field(default_factory=list)
    caption_style: dict[str, Any] = Field(default_factory=dict)
    reframe: ReframeEdit = Field(default_factory=ReframeEdit)
    title: TitleEdit = Field(default_factory=TitleEdit)
    watermark: WatermarkEdit = Field(default_factory=WatermarkEdit)

    def validate_against(self, source_duration: float, min_d: float = 3.0, max_d: float = 180.0) -> "ClipEdit":
        e = self.model_copy(deep=True)
        e.trim_start = max(0.0, min(e.trim_start, source_duration - min_d))
        e.trim_end = max(e.trim_start + min_d, min(e.trim_end, source_duration))
        if e.trim_end - e.trim_start > max_d:
            e.trim_end = e.trim_start + max_d
        e.words = sorted([w for w in e.words if w.w.strip() and w.e > w.s], key=lambda w: w.s)
        return e

    def plan_key(self) -> str:
        r = self.reframe
        m = r.manual if r.mode == "manual" else {}
        return f"{r.mode}|{round(self.trim_start, 2)}|{round(self.trim_end, 2)}|{sorted(m.items())}"


def default_edit(moment, channel_cfg: dict[str, Any], caption_defaults: dict[str, Any]) -> ClipEdit:
    style = copy.deepcopy(caption_defaults)
    if channel_cfg.get("caption_style"):
        style["style"] = channel_cfg["caption_style"]
    return ClipEdit(
        trim_start=round(moment.start - moment.source_start, 3),
        trim_end=round(moment.end - moment.source_start, 3),
        words=[EditWord(**w) for w in (moment.words or [])],
        caption_style=style,
        reframe=ReframeEdit(mode=channel_cfg.get("reframe_mode") or "auto"),
    )


def diff_summary(original: dict[str, Any], edited: dict[str, Any]) -> dict[str, Any]:
    """Compact description of what the user changed (stored with feedback for learning)."""
    out: dict[str, Any] = {}
    for k in ("trim_start", "trim_end"):
        if k in original and k in edited and abs(float(original[k]) - float(edited[k])) > 0.05:
            out[k + "_delta"] = round(float(edited[k]) - float(original[k]), 3)
    ow = [w.get("w") for w in original.get("words", [])]
    ew = [w.get("w") for w in edited.get("words", [])]
    if ow != ew:
        out["words_changed"] = sum(1 for a, b in zip(ow, ew) if a != b) + abs(len(ow) - len(ew))
    if (original.get("caption_style") or {}).get("style") != (edited.get("caption_style") or {}).get("style"):
        out["caption_style"] = (edited.get("caption_style") or {}).get("style")
    if (original.get("reframe") or {}).get("mode") != (edited.get("reframe") or {}).get("mode"):
        out["reframe_mode"] = (edited.get("reframe") or {}).get("mode")
    if (edited.get("title") or {}).get("text"):
        out["title_added"] = True
    return out
