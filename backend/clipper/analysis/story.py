"""Story Detector: narrative structure (introduction → development → conflict → key moment → resolution).

Works on sentences with timestamps. The resulting arc gives natural start/end points so that a
story that needs 45 s gets 45 s instead of being forced into a 15 s cut.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from clipper.analysis import lexicon as L
from clipper.analysis.text import Sentence, count_cues


@dataclass
class StoryArc:
    score: float  # 0..1 narrative strength
    start_index: int | None
    end_index: int | None
    phases: dict[str, list[int]] = field(default_factory=dict)
    speakers: int = 1
    qa_pairs: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class StoryDetector:
    def detect(self, sentences: list[Sentence], peak_times: list[float] | None = None) -> StoryArc:
        if not sentences:
            return StoryArc(0.0, None, None)
        phases: dict[str, list[int]] = {"intro": [], "development": [], "turn": [], "climax": [], "resolution": []}
        for i, s in enumerate(sentences):
            for ph in ("intro", "development", "turn", "resolution"):
                if count_cues(s.text, L.STORY[ph]):
                    phases[ph].append(i)
            if s.is_exclamation or any(s.start - 0.5 <= p <= s.end + 1.5 for p in (peak_times or [])):
                phases["climax"].append(i)
        speakers = len({s.speaker for s in sentences if s.speaker}) or 1
        qa = sum(1 for a, b in zip(sentences, sentences[1:]) if a.is_question and b.speaker and a.speaker and b.speaker != a.speaker)

        intro = phases["intro"][0] if phases["intro"] else None
        score = 0.0
        if intro is not None:
            score += 0.35
            after = lambda ph: [i for i in phases[ph] if i > intro]  # noqa: E731
            if after("development"):
                score += 0.15
            if after("turn"):
                score += 0.2
            if after("climax"):
                score += 0.15
            if after("resolution"):
                score += 0.15
        else:
            # conversational narrative without explicit opener: development + turn + climax
            score += 0.1 * min(2, len(phases["development"])) + 0.15 * bool(phases["turn"]) + 0.1 * bool(phases["climax"])
            score += 0.05 * min(3, qa)
        total = sentences[-1].end - sentences[0].start
        if total < 8:
            score *= 0.6
        score = min(1.0, score)

        start = intro
        end = None
        if phases["resolution"]:
            cand = [i for i in phases["resolution"] if start is None or i > start]
            if cand:
                end = cand[0]
                # include the reaction right after the resolution line if short
                if end + 1 < len(sentences) and sentences[end + 1].duration < 3.5:
                    end += 1
        if end is None and phases["climax"]:
            cand = [i for i in phases["climax"] if start is None or i > start]
            if cand:
                end = min(len(sentences) - 1, cand[-1] + 1)
        return StoryArc(round(score, 3), start, end, phases, speakers, qa)
