"""Caption style presets. Every field can be overridden per clip from the editor."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"

FONT_FAMILIES = ["Montserrat Black", "Montserrat ExtraBold", "Anton", "Bebas Neue", "Poppins ExtraBold",
                 "Inter SemiBold", "Inter ExtraBold", "Bangers"]

PRESETS: dict[str, dict[str, Any]] = {
    # 1. one word at a time, pop-in
    "word": {"mode": "word", "font": "Montserrat Black", "font_size": 96, "uppercase": True, "animation": "pop",
             "outline": 7, "shadow": 3, "highlight_color": "#FFE600", "emphasis": "auto", "position": "middle"},
    # 2. whole phrase, appears with a soft pop
    "phrase": {"mode": "phrase", "font": "Montserrat ExtraBold", "font_size": 76, "uppercase": False, "animation": "fade",
               "outline": 6, "shadow": 2, "max_words_per_line": 5, "max_lines": 2, "emphasis": "auto"},
    # 3. karaoke fill
    "karaoke": {"mode": "karaoke", "font": "Montserrat Black", "font_size": 80, "uppercase": True, "animation": "none",
                "outline": 6, "shadow": 2, "max_words_per_line": 4, "highlight_color": "#FFE600"},
    # 4. dynamic highlight of the current word (classic TikTok/Reels look)
    "dynamic": {"mode": "dynamic", "font": "Montserrat Black", "font_size": 84, "uppercase": True, "animation": "pop",
                "outline": 7, "shadow": 3, "max_words_per_line": 3, "highlight_color": "#FFE600", "highlight_scale": 112},
    # 5. podcast: two calm lines, sentence case, boxed
    "podcast": {"mode": "dynamic", "font": "Poppins ExtraBold", "font_size": 62, "uppercase": False, "animation": "fade",
                "outline": 0, "shadow": 0, "box": True, "box_color": "#000000", "box_opacity": 0.55, "max_words_per_line": 6,
                "max_lines": 2, "highlight_color": "#7CF29A", "highlight_scale": 100, "position": "lower", "margin_v": 330},
    # 6. gaming: huge, punchy, colourful, bouncing
    "gaming": {"mode": "dynamic", "font": "Bangers", "font_size": 104, "uppercase": True, "animation": "bounce",
               "outline": 9, "shadow": 4, "max_words_per_line": 2, "highlight_color": "#39FF14", "highlight_scale": 120,
               "rotate": True, "position": "middle"},
    # 7. minimal: small, clean, fade
    "minimal": {"mode": "phrase", "font": "Inter SemiBold", "font_size": 56, "uppercase": False, "animation": "fade",
                "outline": 2, "shadow": 0, "max_words_per_line": 7, "max_lines": 2, "emphasis": "none", "margin_v": 260},
}

BASE: dict[str, Any] = {
    "style": "dynamic", "mode": "dynamic", "font": "Montserrat Black", "font_size": 84, "primary_color": "#FFFFFF",
    "highlight_color": "#FFE600", "outline_color": "#000000", "shadow_color": "#000000", "outline": 6, "shadow": 3,
    "position": "lower", "margin_v": 420, "pos_x": 0.5, "pos_y": 0.72, "uppercase": True, "max_words_per_line": 4,
    "max_lines": 1, "animation": "pop", "highlight_scale": 110, "emphasis": "auto", "box": False, "box_color": "#000000",
    "box_opacity": 0.5, "rotate": False, "enabled": True,
}

COLOR_SWATCHES = {"yellow": "#FFE600", "purple": "#B266FF", "green": "#39FF14", "cyan": "#00E5FF", "red": "#FF3B3B",
                  "orange": "#FF8A00", "pink": "#FF4FD8", "white": "#FFFFFF"}


def resolve_style(style: dict[str, Any] | None) -> dict[str, Any]:
    style = style or {}
    out = copy.deepcopy(BASE)
    preset = PRESETS.get(style.get("style") or out["style"], {})
    out.update(preset)
    out.update({k: v for k, v in style.items() if v is not None})
    out["style"] = style.get("style") or out["style"]
    return out
