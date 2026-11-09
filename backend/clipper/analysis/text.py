"""Text utilities: normalisation, cue matching and sentence segmentation over timed words."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from clipper.asr.engine import Word


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("ñ", "n")
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=256)
def _phrase_re(phrases: tuple[str, ...]) -> re.Pattern:
    parts = sorted({norm(p) for p in phrases}, key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(re.escape(p) for p in parts) + r")(?![\w])")


def find_cues(text: str, phrases: list[str]) -> list[str]:
    if not phrases or not text:
        return []
    return _phrase_re(tuple(phrases)).findall(norm(text))


def count_cues(text: str, phrases: list[str]) -> int:
    return len(find_cues(text, phrases))


@dataclass
class Sentence:
    i0: int  # first word index (within the word list passed to segment_sentences)
    i1: int  # last word index (inclusive)
    start: float
    end: float
    text: str
    speaker: str | None = None
    pause_before: float = 0.0
    pause_after: float = 0.0
    tags: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def is_question(self) -> bool:
        return self.text.rstrip().endswith("?") or self.text.lstrip().startswith("¿")

    @property
    def is_exclamation(self) -> bool:
        return self.text.rstrip().endswith("!") or self.text.lstrip().startswith("¡")


_END = re.compile(r"[.!?…]+[\"'»”)]*$")


def segment_sentences(words: list[Word], max_pause: float = 0.9, max_len_s: float = 14.0) -> list[Sentence]:
    """Split at sentence-final punctuation, long pauses, speaker changes or excessive length."""
    out: list[Sentence] = []
    if not words:
        return out
    start = 0
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        boundary = nxt is None
        if nxt is not None:
            gap = nxt.s - w.e
            if _END.search(w.w) or gap >= max_pause or (w.spk and nxt.spk and w.spk != nxt.spk) \
                    or (w.e - words[start].s) >= max_len_s:
                boundary = True
        if boundary:
            ws = words[start: i + 1]
            pb = ws[0].s - words[start - 1].e if start > 0 else 1.0
            pa = nxt.s - w.e if nxt is not None else 1.0
            spk = max({x.spk for x in ws}, key=lambda s: sum(1 for x in ws if x.spk == s)) if ws[0].spk else None
            out.append(Sentence(start, i, ws[0].s, ws[-1].e, " ".join(x.w for x in ws), spk, max(0.0, pb), max(0.0, pa)))
            start = i + 1
    return out


def starts_with_anaphora(text: str, starters: list[str]) -> bool:
    first = norm(text).lstrip("¿¡\"'«( ").split(" ", 1)[0].strip(",.;:")
    return first in {norm(s) for s in starters}


def shingles(text: str, n: int = 3) -> set[tuple[str, ...]]:
    toks = re.findall(r"\w+", norm(text))
    return {tuple(toks[i: i + n]) for i in range(max(0, len(toks) - n + 1))}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def shorten(text: str, max_chars: int = 70) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"
