"""Universal Content Classifier.

Combines platform metadata, transcript vocabulary, visual statistics and (optionally) an LLM into a
primary category plus secondary categories. The classification only *re-weights* scoring; it never
restricts which kinds of moments can be detected. It is re-evaluated continuously with hysteresis.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

import numpy as np

from clipper.analysis import lexicon as L
from clipper.analysis.text import count_cues, norm


@dataclass
class Classification:
    primary: str
    secondary: list[str]
    scores: dict[str, float]
    confidence: float
    sources: list[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)
    changed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def map_platform_category(name: str | None) -> str | None:
    if not name:
        return None
    key = norm(name)
    if key in L.PLATFORM_CATEGORY_MAP:
        return L.PLATFORM_CATEGORY_MAP[key]
    # On Twitch/Kick an unknown category is almost always a game title
    return "gaming"


class ContentClassifier:
    def __init__(self, platform_category: str | None = None, decay: float = 0.85, switch_margin: float = 0.08) -> None:
        self.platform_category = platform_category
        self.decay = decay
        self.switch_margin = switch_margin
        self._scores = {c: 0.0 for c in L.CATEGORIES}
        self._current: Classification | None = None
        self._pending_switch: str | None = None
        self.llm_hint: dict[str, float] = {}

    def set_platform_category(self, name: str | None) -> None:
        self.platform_category = name

    def _lexical(self, text: str) -> dict[str, float]:
        words = max(1, len(text.split()))
        out = {}
        for cat, terms in L.CATEGORY_TERMS.items():
            out[cat] = count_cues(text, terms) / words * 100  # hits per 100 words
        humor = count_cues(text, L.HUMOR) / words * 100
        out["entertainment"] = out.get("entertainment", 0) + 0.5 * humor
        out["debate"] = out.get("debate", 0) + 0.4 * count_cues(text, L.HOOK["conflict"]) / words * 100
        out["education"] = out.get("education", 0) + 0.3 * count_cues(text, L.VALUE) / words * 100
        out["podcast"] = out.get("podcast", 0) + 0.3 * count_cues(text, L.STORY["intro"]) / words * 100
        out["motivational"] = out.get("motivational", 0) + 0.2 * count_cues(text, L.OPINION) / words * 100
        return out

    @staticmethod
    def _visual(vs) -> dict[str, float]:
        if vs is None or vs.frames == 0:
            return {}
        out = {c: 0.0 for c in L.CATEGORIES}
        talking = vs.faces_mean >= 0.8 and vs.motion_mean < 0.04
        if talking and vs.max_faces >= 2:
            out["podcast"] += 0.6
            out["debate"] += 0.3
        elif talking:
            out["podcast"] += 0.3
            out["education"] += 0.2
            out["entertainment"] += 0.2
        if vs.face_area_mean < 0.02 and vs.motion_mean > 0.05:
            out["gaming"] += 0.5
            out["sports"] += 0.2
        if vs.motion_mean > 0.08 and vs.faces_mean > 0.3:
            out["irl"] += 0.5
        if vs.edge_density > 0.12 and vs.motion_mean < 0.02:
            out["education"] += 0.4
            out["tech"] += 0.2
        return out

    def update(self, transcript_text: str, visual_summary=None) -> Classification:
        lex = self._lexical(transcript_text) if transcript_text else {}
        vis = self._visual(visual_summary)
        plat = map_platform_category(self.platform_category)
        sources = []
        for c in L.CATEGORIES:
            evidence = 0.0
            if lex:
                evidence += min(1.0, lex.get(c, 0.0) / 3.0) * 0.5
            if vis:
                evidence += vis.get(c, 0.0) * 0.25
            if plat == c:
                evidence += 0.35
            if self.llm_hint:
                evidence += self.llm_hint.get(c, 0.0) * 0.4
            self._scores[c] = self.decay * self._scores[c] + (1 - self.decay) * evidence
        if lex:
            sources.append("transcript")
        if vis:
            sources.append("visual")
        if plat:
            sources.append("platform")
        if self.llm_hint:
            sources.append("llm")
        ranked = sorted(self._scores.items(), key=lambda kv: -kv[1])
        leader, lead_score = ranked[0]
        if lead_score <= 1e-6:
            leader = plat or "entertainment"
        changed = False
        if self._current is None:
            primary = leader
            changed = True
        else:
            primary = self._current.primary
            if leader != primary and lead_score > self._scores.get(primary, 0) + self.switch_margin:
                if self._pending_switch == leader:  # confirmed on two consecutive updates
                    primary, changed = leader, True
                    self._pending_switch = None
                else:
                    self._pending_switch = leader
            else:
                self._pending_switch = None
        top = self._scores.get(primary, 0.0)
        secondary = [c for c, v in ranked if c != primary and v > 0.5 * top and v > 0.02][:3]
        vals = np.array([v for _, v in ranked])
        conf = float((vals[0] - vals[1]) / (vals[0] + 1e-6)) if len(vals) > 1 and vals[0] > 0 else 0.0
        self._current = Classification(primary, secondary, {k: round(v, 4) for k, v in ranked}, round(min(1.0, 0.3 + conf), 3),
                                       sources, changed=changed)
        return self._current

    @property
    def current(self) -> Classification | None:
        return self._current
