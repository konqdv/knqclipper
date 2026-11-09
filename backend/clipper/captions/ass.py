"""Caption Engine: word-timed animated subtitles rendered as ASS (libass via FFmpeg).

Modes: ``word`` (one word at a time), ``phrase`` (whole phrase), ``karaoke`` (progressive fill),
``dynamic`` (phrase with the spoken word highlighted). Presets ``podcast``, ``gaming`` and
``minimal`` are built on these modes. Words are rendered exactly as transcribed (only optional
upper-casing for display); the transcript itself is never altered here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from clipper.analysis import lexicon as L
from clipper.analysis.text import norm
from clipper.captions.styles import resolve_style


@dataclass
class CapWord:
    text: str
    start: float
    end: float
    emphasis: bool = False


def ass_color(hex_color: str, alpha: float = 0.0) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = int(round(max(0.0, min(1.0, alpha)) * 255))
    return f"&H{a:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


_EMPH = {norm(x) for x in L.STRONG + L.EXCLAMATIONS}
_EMPH_PHRASE_WORDS = {w for p in L.HOOK["surprise"] + L.HOOK["curiosity"] for w in norm(p).split() if len(w) > 3}


def auto_emphasis(word: str) -> bool:
    w = norm(re.sub(r"[^\w]", "", word))
    if not w:
        return False
    return w in _EMPH or w in _EMPH_PHRASE_WORDS or any(ch.isdigit() for ch in w) or (word.isupper() and len(word) > 2)


def prepare_words(words: list[dict[str, Any]], offset: float, duration: float, style: dict[str, Any]) -> list[CapWord]:
    out: list[CapWord] = []
    for w in words:
        s, e = float(w["s"]) - offset, float(w["e"]) - offset
        if e <= 0 or s >= duration:
            continue
        text = str(w["w"]).strip()
        if not text:
            continue
        display = text.upper() if style.get("uppercase") else text
        emph = bool(w.get("emphasis")) if "emphasis" in w else (style.get("emphasis") == "auto" and auto_emphasis(text))
        out.append(CapWord(display, max(0.0, s), min(duration, max(e, s + 0.08)), emph))
    for a, b in zip(out, out[1:]):  # close tiny gaps so captions don't flicker
        if 0 < b.start - a.end < 0.25:
            a.end = b.start
    return out


def group_pages(words: list[CapWord], max_words: int, max_lines: int = 1, max_chars: int = 22) -> list[list[CapWord]]:
    pages: list[list[CapWord]] = []
    cur: list[CapWord] = []
    limit = max_words * max(1, max_lines)
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        chars = sum(len(x.text) + 1 for x in cur)
        brk = (len(cur) >= limit or chars >= max_chars * max(1, max_lines) or re.search(r"[.!?…]$", w.text)
               or (nxt is not None and nxt.start - w.end > 0.6))
        if brk or nxt is None:
            pages.append(cur)
            cur = []
    return pages


def _lines(page: list[CapWord], max_words: int, max_lines: int) -> list[list[CapWord]]:
    if max_lines <= 1 or len(page) <= max_words:
        return [page]
    half = (len(page) + 1) // 2
    return [page[:half], page[half:]]


class CaptionRenderer:
    def __init__(self, width: int = 1080, height: int = 1920) -> None:
        self.w = width
        self.h = height

    def _alignment(self, st: dict[str, Any]) -> tuple[int, int, str]:
        pos = st.get("position", "lower")
        if pos == "top":
            return 8, int(st.get("margin_v", 260)), ""
        if pos == "middle":
            return 5, 0, ""
        if pos == "custom":
            x = int(float(st.get("pos_x", 0.5)) * self.w)
            y = int(float(st.get("pos_y", 0.72)) * self.h)
            return 5, 0, f"\\pos({x},{y})"
        return 2, int(st.get("margin_v", 420)), ""

    def header(self, st: dict[str, Any], title: dict[str, Any] | None, watermark: dict[str, Any] | None) -> str:
        align, margin_v, _ = self._alignment(st)
        prim = ass_color(st["primary_color"])
        hi = ass_color(st["highlight_color"])
        outline_c = ass_color(st["outline_color"])
        back = ass_color(st.get("box_color", "#000000"), 1 - float(st.get("box_opacity", 0.5))) if st.get("box") else ass_color(st.get("shadow_color", "#000000"), 0.35)
        border_style = 3 if st.get("box") else 1
        outline = max(6, int(st.get("outline", 6))) if st.get("box") else int(st.get("outline", 6))
        karaoke = st.get("mode") == "karaoke"
        # In ASS karaoke PrimaryColour is the "sung" colour and SecondaryColour the colour before it.
        p1, p2 = (hi, prim) if karaoke else (prim, hi)
        styles = [
            f"Style: Cap,{st['font']},{int(st['font_size'])},{p1},{p2},{outline_c},{back},0,0,0,0,100,100,0,0,"
            f"{border_style},{outline},{int(st.get('shadow', 0))},{align},60,60,{margin_v},1",
        ]
        if title:
            ts = title.get("style", {})
            styles.append(
                f"Style: Title,{ts.get('font', 'Montserrat Black')},{int(ts.get('font_size', 72))},{ass_color(ts.get('color', '#FFFFFF'))},"
                f"{ass_color(ts.get('color', '#FFFFFF'))},{ass_color(ts.get('box_color', '#000000'))},{ass_color(ts.get('box_color', '#000000'), 0.15)},"
                f"0,0,0,0,100,100,0,0,3,18,0,8,80,80,{int(ts.get('margin_v', 200))},1")
        if watermark and watermark.get("text"):
            align_wm = {"top-left": 7, "top-right": 9, "bottom-left": 1, "bottom-right": 3}.get(watermark.get("position", "top-right"), 9)
            styles.append(
                f"Style: Watermark,{watermark.get('font', 'Inter SemiBold')},{int(watermark.get('font_size', 40))},"
                f"{ass_color(watermark.get('color', '#FFFFFF'), 1 - float(watermark.get('opacity', 0.6)))},&H00FFFFFF,"
                f"{ass_color('#000000', 0.6)},&H00000000,0,0,0,0,100,100,0,0,1,2,0,{align_wm},50,50,60,1")
        return "\n".join([
            "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {self.w}", f"PlayResY: {self.h}", "WrapStyle: 0",
            "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "", "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            *styles, "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ])

    @staticmethod
    def _entry(st: dict[str, Any], dur_ms: int) -> str:
        anim = st.get("animation", "pop")
        if anim == "pop":
            return "\\fscx80\\fscy80\\t(0,90,\\fscx106\\fscy106)\\t(90,160,\\fscx100\\fscy100)"
        if anim == "bounce":
            return "\\fscx60\\fscy60\\t(0,110,\\fscx118\\fscy118)\\t(110,190,\\fscx94\\fscy94)\\t(190,250,\\fscx100\\fscy100)"
        if anim == "fade":
            return f"\\fad({min(120, dur_ms // 3)},{min(80, dur_ms // 4)})"
        return ""

    def _word_tag(self, w: CapWord, st: dict[str, Any], current: bool) -> str:
        txt = escape(w.text)
        if current:
            sc = int(st.get("highlight_scale", 110))
            return f"{{\\c{ass_color(st['highlight_color'])}\\fscx{sc}\\fscy{sc}}}{txt}{{\\r}}"
        if w.emphasis:
            return f"{{\\c{ass_color(st['highlight_color'])}}}{txt}{{\\r}}"
        return txt

    def events(self, words: list[CapWord], st: dict[str, Any]) -> list[str]:
        mode = st.get("mode", "dynamic")
        _, _, pos_tag = self._alignment(st)
        max_words = int(st.get("max_words_per_line", 4))
        max_lines = int(st.get("max_lines", 1))
        out: list[str] = []
        rot_flip = False

        def rot() -> str:
            nonlocal rot_flip
            if not st.get("rotate"):
                return ""
            rot_flip = not rot_flip
            return "\\frz2" if rot_flip else "\\frz-2"

        if mode == "word":
            for w in words:
                dur = int((w.end - w.start) * 1000)
                color = f"\\c{ass_color(st['highlight_color'])}" if w.emphasis else ""
                out.append(f"Dialogue: 0,{ass_time(w.start)},{ass_time(w.end)},Cap,,0,0,0,,{{{pos_tag}{self._entry(st, dur)}{rot()}{color}}}{escape(w.text)}")
            return out
        pages = group_pages(words, max_words, max_lines)
        for page in pages:
            p0, p1 = page[0].start, page[-1].end
            dur_ms = int((p1 - p0) * 1000)
            lines = _lines(page, max_words, max_lines)
            if mode == "phrase":
                body = "\\N".join(" ".join(self._word_tag(w, st, False) for w in ln) for ln in lines)
                out.append(f"Dialogue: 0,{ass_time(p0)},{ass_time(p1)},Cap,,0,0,0,,{{{pos_tag}{self._entry(st, dur_ms)}{rot()}}}{body}")
            elif mode == "karaoke":
                parts = []
                cursor = p0
                for ln_i, ln in enumerate(lines):
                    for w in ln:
                        lead = max(0, int(round((w.start - cursor) * 100)))
                        if lead:
                            parts.append(f"{{\\k{lead}}}")
                        parts.append(f"{{\\kf{max(1, int(round((w.end - w.start) * 100)))}}}{escape(w.text)} ")
                        cursor = w.end
                    if ln_i < len(lines) - 1:
                        parts.append("\\N")
                out.append(f"Dialogue: 0,{ass_time(p0)},{ass_time(p1)},Cap,,0,0,0,,{{{pos_tag}{self._entry(st, dur_ms)}}}{''.join(parts).rstrip()}")
            else:  # dynamic: one event per word interval, page entry animation on the first one only
                r = rot()
                for i, cur in enumerate(page):
                    s = cur.start if i > 0 else p0
                    e = page[i + 1].start if i + 1 < len(page) else p1
                    body = "\\N".join(" ".join(self._word_tag(w, st, w is cur) for w in ln) for ln in lines)
                    entry = self._entry(st, dur_ms) if i == 0 else ""
                    out.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,{{{pos_tag}{entry}{r}}}{body}")
        return out

    def build(self, words: list[dict[str, Any]], *, offset: float, duration: float, style: dict[str, Any] | None = None,
              title: dict[str, Any] | None = None, watermark: dict[str, Any] | None = None) -> str:
        st = resolve_style(style)
        title = title if title and title.get("text") else None
        lines = [self.header(st, title, watermark)]
        if st.get("enabled", True):
            lines += self.events(prepare_words(words, offset, duration, st), st)
        if title:
            tdur = float(title.get("duration", 3.0))
            lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(min(duration, tdur))},Title,,0,0,0,,{{\\fad(150,250)}}{escape(title['text'])}")
        if watermark and watermark.get("text"):
            lines.append(f"Dialogue: 2,{ass_time(0)},{ass_time(duration)},Watermark,,0,0,0,,{escape(watermark['text'])}")
        return "\n".join(lines) + "\n"
