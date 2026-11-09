"""Transcript cleanup that never changes what was said.

* removes Whisper hallucinations typical of silence/music (credits, "thanks for watching", loops),
* normalises whitespace and capitalises sentence starts,
* keeps every spoken word verbatim (no paraphrasing, no profanity rewriting).
"""

from __future__ import annotations

import re

from clipper.asr.engine import ASRSegment

_HALLUCINATIONS = [
    r"subt[ií]tulos (realizados|por) (por )?la comunidad de amara",
    r"amara\.org",
    r"subtitulado por",
    r"thanks? for watching",
    r"gracias por ver( el v[ií]deo)?",
    r"suscr[ií]bete",
    r"please subscribe",
    r"www\.[a-z0-9-]+\.(com|org|net)",
    r"^\W*(m[uú]sica|music|applause|aplausos)\W*$",
]
_HALLU_RE = re.compile("|".join(_HALLUCINATIONS), re.IGNORECASE)


def is_hallucination(seg: ASRSegment, rms_db: float | None = None) -> bool:
    text = seg.text.strip()
    if not text:
        return True
    if seg.no_speech_prob > 0.7 and seg.avg_logprob < -0.8:
        return True
    if _HALLU_RE.search(text) and (rms_db is None or rms_db < -35 or seg.avg_logprob < -0.6):
        return True
    words = text.lower().split()
    if len(words) >= 8 and len(set(words)) <= 2:  # "la la la la ..." loops
        return True
    return False


def dedupe_loops(segments: list[ASRSegment]) -> list[ASRSegment]:
    out: list[ASRSegment] = []
    for s in segments:
        if out and s.text.strip().lower() == out[-1].text.strip().lower() and len(s.text) > 12:
            continue
        out.append(s)
    return out


def tidy_words(segments: list[ASRSegment]) -> None:
    """Capitalise the first word after sentence-final punctuation; collapse spaces. In place."""
    cap_next = True
    for seg in segments:
        for w in seg.words:
            w.w = re.sub(r"\s+", " ", w.w).strip()
            if cap_next and w.w and w.w[0].isalpha() and w.w[0].islower():
                w.w = w.w[0].upper() + w.w[1:]
            cap_next = bool(re.search(r"[.!?…]$", w.w))
        seg.text = " ".join(w.w for w in seg.words) if seg.words else seg.text.strip()
