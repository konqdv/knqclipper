"""Natural clip boundaries.

* Start: as close as possible to the interesting part, but at a sentence start that does not depend
  on missing context (otherwise move back to include it); prefer story openers and strong hooks.
* End: after the sentence that closes the idea (story resolution / reaction), never mid-word.
* Duration: within [min, max] and, when learned, close to the creator/category preference.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clipper.analysis import lexicon as L
from clipper.analysis.hook import score_sentence
from clipper.analysis.story import StoryArc
from clipper.analysis.text import Sentence, starts_with_anaphora
from clipper.asr.engine import Word


@dataclass
class Bounds:
    i0: int
    i1: int
    start: float
    end: float
    notes: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


def anchor_index(sentences: list[Sentence], t: float) -> int:
    for i, s in enumerate(sentences):
        if s.start <= t <= s.end + 0.3:
            return i
    before = [i for i, s in enumerate(sentences) if s.start <= t]
    return before[-1] if before else 0


def choose_sentence_bounds(
    sentences: list[Sentence], anchor_t: float, *, min_d: float, max_d: float,
    pref: tuple[float, float] | None = None, story: StoryArc | None = None,
    reaction_end: float | None = None, arousal_at=None,
) -> tuple[int, int, list[str]]:
    notes: list[str] = []
    n = len(sentences)
    a = anchor_index(sentences, anchor_t)
    must_end = max(anchor_t, reaction_end or 0.0) + 0.3
    # --- end -------------------------------------------------------------------
    j = a
    while j < n - 1 and (sentences[j].end < must_end or (sentences[j].pause_after < 0.35 and sentences[j].text[-1:] not in ".!?…")):
        if sentences[j + 1].end - sentences[a].start > max_d:
            break
        j += 1
    if story and story.score >= 0.5 and story.end_index is not None and story.end_index >= j:
        if sentences[story.end_index].end - sentences[min(a, story.start_index or a)].start <= max_d:
            j = story.end_index
            notes.append("end at story resolution")
    # --- start -----------------------------------------------------------------
    best_i, best_val = a, float("-inf")
    for i in range(a, -1, -1):
        dur = sentences[j].end - sentences[i].start
        if dur > max_d:
            break
        s = sentences[i]
        hook = score_sentence(s, arousal_at(s.start, s.end) if arousal_at else 0.0)
        val = 1.2 * hook.score
        if starts_with_anaphora(s.text, L.ANAPHORA_STARTERS):
            val -= 0.8
        if story and story.start_index == i:
            val += 0.7
        if s.pause_before >= 0.5:
            val += 0.2
        if i > 0 and s.speaker and sentences[i - 1].speaker and s.speaker != sentences[i - 1].speaker:
            val += 0.15
        val -= 0.35 * (dur / max_d)
        if dur < min_d:
            val -= 0.6 * (min_d - dur) / min_d
        if pref:
            lo, hi = pref
            if dur < lo:
                val -= 0.4 * (lo - dur) / max(lo, 1)
            elif dur > hi:
                val -= 0.4 * (dur - hi) / max(hi, 1)
        if val > best_val:
            best_i, best_val = i, val
    i0 = best_i
    # --- enforce minimum duration by extending forward, then backward -----------
    while sentences[j].end - sentences[i0].start < min_d and j < n - 1:
        j += 1
        notes.append("extended end for min duration")
    while sentences[j].end - sentences[i0].start < min_d and i0 > 0:
        i0 -= 1
        notes.append("extended start for min duration")
    if sentences[j].end - sentences[i0].start < min_d:
        notes.append("shorter than min duration: not enough speech in window")
    return i0, j, notes


def snap_times(words: list[Word], sentences: list[Sentence], i0: int, i1: int, *,
               reaction_end: float | None = None, lead: float = 0.25, tail: float = 0.5,
               window: tuple[float, float] | None = None) -> tuple[float, float]:
    """Convert sentence indices to times with natural padding, never inside a word."""
    s0, s1 = sentences[i0], sentences[i1]
    w_first, w_last = words[s0.i0], words[s1.i1]
    prev_end = words[s0.i0 - 1].e if s0.i0 > 0 else None
    next_start = words[s1.i1 + 1].s if s1.i1 + 1 < len(words) else None
    start = w_first.s - lead
    if prev_end is not None:
        start = max(start, prev_end + 0.05)
    end = w_last.e + tail
    if reaction_end is not None and w_last.e < reaction_end < w_last.e + 3.0:
        end = max(end, reaction_end + 0.25)
    if next_start is not None:
        end = min(end, max(w_last.e + 0.05, next_start - 0.05))
    if window:
        start = max(start, window[0])
        end = min(end, window[1])
    return round(max(0.0, start), 3), round(end, 3)


def nonspeech_bounds(anchor_t: float, window: tuple[float, float], min_d: float, pre: float = 10.0, post: float = 8.0) -> tuple[float, float]:
    """Boundaries for moments without speech (gameplay, sports, music): around the visual/audio peak."""
    start = max(window[0], anchor_t - pre)
    end = min(window[1], anchor_t + post)
    if end - start < min_d:
        extra = min_d - (end - start)
        start = max(window[0], start - extra * 0.6)
        end = min(window[1], end + extra * 0.4)
    return round(start, 3), round(end, 3)
