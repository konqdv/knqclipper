"""Hook Detector: which *verbatim* sentence can open a clip and grab attention in the first seconds.

Each hook candidate reports whether it depends on previous context (e.g. it opens with "and then",
"that", "pero eso..."), so the boundary selector can prepend the minimum context needed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from clipper.analysis import lexicon as L
from clipper.analysis.text import Sentence, count_cues, find_cues, norm, starts_with_anaphora

HOOK_TYPE_WEIGHT = {
    "curiosity": 1.0, "surprise": 0.85, "conflict": 0.9, "revelation": 1.0, "controversy": 0.95,
    "tension": 0.8, "question": 0.8, "humor": 0.75, "story": 0.85, "value": 0.8,
}


@dataclass
class HookCandidate:
    sentence_index: int
    text: str
    start: float
    score: float  # 0..1
    types: list[str] = field(default_factory=list)
    needs_context: bool = False
    cues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def score_sentence(s: Sentence, arousal: float = 0.0) -> HookCandidate:
    raw = 0.0
    types: list[str] = []
    cues: list[str] = []
    for kind, phrases in L.HOOK.items():
        found = find_cues(s.text, phrases)
        if found:
            types.append(kind)
            cues += found
            raw += HOOK_TYPE_WEIGHT[kind] * (1 + 0.3 * (len(found) - 1))
    first = norm(s.text).lstrip("¿¡ ")
    if s.is_question or any(first.startswith(norm(q)) for q in L.QUESTION_STARTERS):
        types.append("question")
        raw += HOOK_TYPE_WEIGHT["question"] * (1.0 if s.is_question else 0.6)
    if count_cues(s.text, L.STORY["intro"]):
        types.append("story")
        raw += HOOK_TYPE_WEIGHT["story"]
    if count_cues(s.text, L.HUMOR):
        types.append("humor")
        raw += HOOK_TYPE_WEIGHT["humor"]
    if count_cues(s.text, L.VALUE) >= 1:
        types.append("value")
        raw += 0.5 * HOOK_TYPE_WEIGHT["value"]
    strong = count_cues(s.text, L.STRONG)
    raw += 0.25 * min(strong, 2)
    if s.is_exclamation:
        raw += 0.3
    if any(ch.isdigit() for ch in s.text):
        raw += 0.15  # concrete numbers ("3 cosas", "$10.000") tend to hook
    n_words = len(s.text.split())
    if n_words < 3:
        raw *= 0.6  # too short to carry meaning on its own
    elif n_words > 35:
        raw *= 0.7  # rambling first sentence
    raw += 0.6 * max(0.0, arousal - 0.5)
    needs_ctx = starts_with_anaphora(s.text, L.ANAPHORA_STARTERS)
    if needs_ctx:
        raw *= 0.75
    score = float(1 - np.exp(-raw))
    return HookCandidate(-1, s.text, s.start, score, types, needs_ctx, cues)


class HookDetector:
    def candidates(self, sentences: list[Sentence], arousal_at=None) -> list[HookCandidate]:
        out = []
        for i, s in enumerate(sentences):
            ar = arousal_at(s.start, s.end) if arousal_at else 0.0
            c = score_sentence(s, ar)
            c.sentence_index = i
            out.append(c)
        return out

    def best_opening(self, sentences: list[Sentence], first_n: int = 2, arousal_at=None) -> HookCandidate | None:
        """Hook strength of how a clip starting at ``sentences[0]`` opens (first ``first_n`` sentences)."""
        if not sentences:
            return None
        cands = self.candidates(sentences[:first_n], arousal_at)
        best = max(cands, key=lambda c: c.score * (1.0 if c.sentence_index == 0 else 0.8))
        return best
