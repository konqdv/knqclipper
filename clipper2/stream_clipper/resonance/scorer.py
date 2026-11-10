"""
Core resonance scoring algorithm.

Builds multiple 1-second signals, then combines them into one highlight score.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

import numpy as np

from ..asr.transcriber import Segment
from ..danmaku.models import DanmakuComment
from .keywords import EXCITEMENT_KEYWORDS, excitement_ratio, sentiment_ratios


_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_WHITESPACE_RE = re.compile(r"\s+")
_CJK_STOPCHARS: set[str] = set("的一了是在有我你他她它们吗呢吧啊呀哦嗯这那就都还没很和与及被把对给让从上到中下里外前后再又也才并但而且如果因为所以")


def _cjk_chars(text: str) -> set[str]:
    return set(_CJK_RE.findall(text))


def _normalize_text(s: str) -> str:
    return _WHITESPACE_RE.sub("", (s or "").strip())


# Cross-language excitement cues for ASR captions (used when there is no chat).
_EMPHASIS_RE = re.compile(r"[!¡?¿]")
_LAUGH_RE = re.compile(
    r"(?:a*(?:ja|ha|je|he){2,}|ja+j+|lol|lmao|rofl|xd+)", re.IGNORECASE
)
_ELONG_RE = re.compile(r"(\w)\1{2,}", re.UNICODE)


def _speech_char_count(text: str) -> int:
    """Non-whitespace character count: works for any language/script."""
    return len(_WHITESPACE_RE.sub("", text or ""))


def _generic_excitement(text: str) -> float:
    """
    Language-agnostic excitement cue from a single ASR caption line.

    Looks at exclamation/question marks, laughter ("jaja", "lol"), and
    elongated words ("goool", "siii"), plus the original keyword lexicon so
    Chinese content keeps working. Returns a value in [0, 1].
    """
    if not text:
        return 0.0
    score = 0.0
    score += min(3, len(_EMPHASIS_RE.findall(text))) / 3.0 * 0.5
    if _LAUGH_RE.search(text):
        score += 0.5
    if _ELONG_RE.search(text):
        score += 0.3
    if any(kw in text for kw in EXCITEMENT_KEYWORDS):
        score += 0.5
    return min(1.0, score)


def _normalize01(arr: np.ndarray) -> np.ndarray:
    if arr.size == 0:
        return arr
    max_v = float(np.max(arr))
    if max_v <= 1e-12:
        return np.zeros_like(arr, dtype=np.float64)
    return np.asarray(arr, dtype=np.float64) / max_v


def _resample_signal(signal: Optional[Sequence[float]], n: int) -> np.ndarray:
    if signal is None:
        return np.zeros(n, dtype=np.float64)
    arr = np.asarray(signal, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        return np.zeros(n, dtype=np.float64)
    if arr.size == n:
        return arr
    if arr.size == 1:
        return np.full(n, float(arr[0]), dtype=np.float64)
    x_old = np.linspace(0.0, 1.0, num=arr.size, dtype=np.float64)
    x_new = np.linspace(0.0, 1.0, num=n, dtype=np.float64)
    return np.interp(x_new, x_old, arr)


def compute_scores(
    comments: List[DanmakuComment],
    segments: List[Segment],
    duration: float,
    window: float = 10.0,
    weights: Tuple[float, float, float] = (0.4, 0.4, 0.2),
    *,
    audio_energy: Optional[Sequence[float]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute per-second resonance scores.

    The `weights` tuple keeps API compatibility:
    - weights[0]: activity branch (density + burst + audio energy)
    - weights[1]: emotion branch (excitement + repetition + sentiment)
    - weights[2]: subtitle overlap branch
    """
    n = max(1, int(duration))
    times = np.arange(n, dtype=np.float64) + 0.5

    w1, w2, w3 = weights
    half = window / 2.0
    half_buckets = int(half) + 1

    energy_raw = _resample_signal(audio_energy, n)
    energy_norm = _normalize01(np.clip(energy_raw, 0.0, None))
    energy_velocity = np.diff(np.r_[energy_norm[0], energy_norm])
    energy_velocity = _normalize01(np.clip(energy_velocity, 0.0, None))
    energy_signal = 0.7 * energy_norm + 0.3 * energy_velocity

    comment_buckets: list[list[DanmakuComment]] = [[] for _ in range(n)]
    for c in comments:
        idx = min(int(c.time_offset), n - 1)
        if 0 <= idx < n:
            comment_buckets[idx].append(c)

    subtitle_buckets: list[str] = [""] * n
    for seg in segments:
        s_start = max(0, int(seg.start))
        s_end = min(n - 1, int(seg.end))
        for idx in range(s_start, s_end + 1):
            subtitle_buckets[idx] += seg.text

    if len(comments) == 0:
        return _asr_only_scores(n, times, subtitle_buckets, half_buckets, energy_signal)

    density_raw = np.zeros(n, dtype=np.float64)
    excitement_arr = np.zeros(n, dtype=np.float64)
    repetition_arr = np.zeros(n, dtype=np.float64)
    overlap_arr = np.zeros(n, dtype=np.float64)
    sentiment_intensity = np.zeros(n, dtype=np.float64)
    sentiment_polarity = np.zeros(n, dtype=np.float64)

    for i in range(n):
        lo = max(0, i - half_buckets)
        hi = min(n, i + half_buckets + 1)

        window_comments: list[DanmakuComment] = []
        for b in range(lo, hi):
            window_comments.extend(comment_buckets[b])

        n_comments = len(window_comments)
        density_raw[i] = n_comments / max(window, 1e-6)
        if n_comments == 0:
            continue

        texts = [c.text for c in window_comments]
        excitement_arr[i] = excitement_ratio(texts)

        pos_ratio, neg_ratio = sentiment_ratios(texts)
        sentiment_intensity[i] = max(0.0, min(1.0, pos_ratio + neg_ratio))
        sentiment_polarity[i] = max(-1.0, min(1.0, pos_ratio - neg_ratio))

        normalized = [_normalize_text(t) for t in texts if _normalize_text(t)]
        if len(normalized) >= 2:
            unique_ratio = len(set(normalized)) / len(normalized)
            repetition_arr[i] = max(0.0, min(1.0, 1.0 - unique_ratio))

        sub_text = "".join(subtitle_buckets[lo:hi])
        if sub_text.strip():
            danmaku_chars = _cjk_chars(" ".join(texts)) - _CJK_STOPCHARS
            subtitle_chars = _cjk_chars(sub_text) - _CJK_STOPCHARS
            union = danmaku_chars | subtitle_chars
            if union:
                overlap_arr[i] = len(danmaku_chars & subtitle_chars) / len(union)

    density_norm = _normalize01(np.clip(density_raw, 0.0, None))

    # Burst signal: "sudden increase" is stronger than "always high".
    window_shift = max(1, int(round(window)))
    prev_idx = np.maximum(np.arange(n) - window_shift, 0)
    velocity_raw = density_raw - density_raw[prev_idx]
    velocity_norm = _normalize01(np.clip(velocity_raw, 0.0, None))

    # Normalize polarity from [-1, 1] to [0, 1].
    sentiment_polarity_norm = (sentiment_polarity + 1.0) / 2.0

    activity_signal = (
        0.50 * density_norm
        + 0.25 * velocity_norm
        + 0.25 * energy_signal
    )
    emotion_signal = (
        0.55 * excitement_arr
        + 0.20 * repetition_arr
        + 0.15 * sentiment_intensity
        + 0.10 * sentiment_polarity_norm
    )

    scores = w1 * activity_signal + w2 * emotion_signal + w3 * overlap_arr
    return times, np.clip(scores, 0.0, 1.0)


def _asr_only_scores(
    n: int,
    times: np.ndarray,
    subtitle_buckets: list[str],
    half_buckets: int,
    energy_signal: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Fallback scoring when there is no danmaku/chat — e.g. live recordings or
    VODs whose chat could not be captured.

    Language-agnostic: combines how much is being said (speech density and its
    sudden increases), cross-language excitement cues, and audio loudness /
    dynamics. Every sub-signal is normalized to [0, 1] so the combined score
    uses the full range.

    (An earlier version only counted CJK characters and Chinese keywords, which
    left Spanish/English content with a near-flat signal and produced no clips.)
    """
    bucket_chars = np.array(
        [_speech_char_count(t) for t in subtitle_buckets], dtype=np.float64
    )
    bucket_excite = np.array(
        [_generic_excitement(t) for t in subtitle_buckets], dtype=np.float64
    )

    speech_density = np.zeros(n, dtype=np.float64)
    excitement = np.zeros(n, dtype=np.float64)
    for i in range(n):
        lo = max(0, i - half_buckets)
        hi = min(n, i + half_buckets + 1)
        speech_density[i] = float(bucket_chars[lo:hi].sum())
        span = max(1, hi - lo)
        excitement[i] = float(bucket_excite[lo:hi].sum()) / span

    speech_norm = _normalize01(speech_density)

    # Burst: a sudden rise in talking is more interesting than steady chatter.
    shift = max(1, half_buckets)
    prev_idx = np.maximum(np.arange(n) - shift, 0)
    speech_velocity = _normalize01(
        np.clip(speech_norm - speech_norm[prev_idx], 0.0, None)
    )
    excite_norm = _normalize01(excitement)

    scores = (
        0.34 * speech_norm
        + 0.16 * speech_velocity
        + 0.18 * excite_norm
        + 0.32 * energy_signal
    )
    return times, np.clip(scores, 0.0, 1.0)



