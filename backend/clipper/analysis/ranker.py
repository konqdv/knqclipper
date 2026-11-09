"""Highlight Ranker: 0-100 score from seven criteria with context-adaptive and learned weights.

Default maxima: hook 25, entertainment 20, narrative 15, emotion 15, flow 10, shareability 10,
audio quality 5. Weights are (1) configurable, (2) re-balanced per content category so that e.g. an
educational clip is not penalised for having no laughs, and (3) adjusted by the feedback model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from clipper.analysis import lexicon as L
from clipper.analysis.text import Sentence, count_cues, norm, starts_with_anaphora
from clipper.asr.engine import Word

CRITERIA = ["hook", "entertainment", "narrative", "emotion", "flow", "shareability", "audio_quality"]

# multipliers applied to the base weights for each primary category (renormalised to 100)
CATEGORY_PROFILES: dict[str, dict[str, float]] = {
    "education": {"entertainment": 0.4, "emotion": 0.6, "narrative": 1.2, "flow": 1.3, "shareability": 1.4},
    "tech": {"entertainment": 0.5, "emotion": 0.6, "narrative": 1.1, "flow": 1.3, "shareability": 1.4},
    "cooking": {"entertainment": 0.6, "emotion": 0.6, "flow": 1.2, "shareability": 1.4},
    "beauty": {"entertainment": 0.6, "emotion": 0.6, "flow": 1.2, "shareability": 1.4},
    "podcast": {"narrative": 1.35, "flow": 1.2, "entertainment": 0.9, "emotion": 0.8},
    "debate": {"emotion": 1.2, "hook": 1.1, "entertainment": 0.7, "shareability": 1.2},
    "news": {"entertainment": 0.5, "emotion": 0.7, "narrative": 1.1, "shareability": 1.35},
    "gaming": {"emotion": 1.3, "entertainment": 1.1, "narrative": 0.6, "flow": 0.8},
    "sports": {"emotion": 1.3, "entertainment": 0.9, "narrative": 0.7},
    "irl": {"entertainment": 1.2, "emotion": 1.1},
    "music": {"audio_quality": 2.0, "emotion": 1.1, "narrative": 0.5, "hook": 0.9},
    "motivational": {"shareability": 1.3, "narrative": 1.1, "entertainment": 0.7},
    "entertainment": {},
}


@dataclass
class RankResult:
    score: float
    subscores: dict[str, float]  # 0..1 per criterion
    points: dict[str, float]  # contribution in points
    weights: dict[str, float]  # effective max points
    priority: str
    confidence: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _sat(x: float) -> float:
    return float(1 - np.exp(-max(0.0, x)))


def effective_weights(base: dict[str, float], category: str | None, adaptive: bool = True,
                      learned: dict[str, float] | None = None) -> dict[str, float]:
    w = {c: float(base.get(c, 0)) for c in CRITERIA}
    total = sum(w.values()) or 100.0
    if adaptive and category in CATEGORY_PROFILES:
        for c, m in CATEGORY_PROFILES[category].items():
            w[c] *= m
    if learned:
        for c, m in learned.items():
            if c in w:
                w[c] *= float(np.clip(m, 0.5, 1.5))
    s = sum(w.values()) or 1.0
    return {c: v * total / s for c, v in w.items()}


def priority_for(score: float, tiers: dict[str, float]) -> str:
    if score >= tiers.get("excellent", 90):
        return "excellent"
    if score >= tiers.get("high", 75):
        return "high"
    if score >= tiers.get("review", 60):
        return "review"
    return "low"


def audio_quality(rms_db: np.ndarray, words: list[Word]) -> float:
    if len(rms_db) < 10:
        return 0.5
    speech = np.percentile(rms_db, 90)
    floor = np.percentile(rms_db, 10)
    snr = float(speech - floor)
    snr_score = float(np.clip((snr - 8) / 25, 0, 1))
    clipping = float(np.mean(rms_db > -1.5))
    level = float(np.clip((speech + 50) / 30, 0, 1))  # too quiet overall is bad
    asr = float(np.mean([w.p for w in words])) if words else 0.6
    return float(np.clip(0.45 * snr_score + 0.2 * level + 0.35 * asr - 2 * clipping, 0, 1))


def compute_subscores(*, sentences: list[Sentence], words: list[Word], emotion, chat, visual, story, hook,
                      context, category: str, rms_db: np.ndarray, duration: float) -> tuple[dict[str, float], list[str]]:
    text = " ".join(s.text for s in sentences)
    nwords = max(1, len(text.split()))
    reasons: list[str] = []
    ev_types = [e.type for e in (emotion.events if emotion else [])]
    ev_strength = {t: max([e.strength for e in emotion.events if e.type == t], default=0) for t in set(ev_types)} if emotion else {}
    chat_emo = (chat.emotions if chat and chat.messages else {}) or {}
    burst = max(0.0, (chat.burst_z if chat and chat.messages else 0.0))

    # 1. hook ---------------------------------------------------------------------
    hook_s = hook.score if hook else 0.0
    if hook and hook.types:
        reasons.append("gancho: " + ", ".join(sorted(set(hook.types))))

    # 2. entertainment / humour ---------------------------------------------------
    humor_cues = count_cues(text, L.HUMOR)
    ent_raw = 1.2 * ev_strength.get("laughter", 0) + 0.25 * humor_cues + 1.0 * chat_emo.get("laugh", 0) * min(1, burst / 3 + 0.3)
    ent_raw += 0.3 * count_cues(text, L.HOOK["surprise"]) + 0.4 * chat_emo.get("hype", 0)
    if category == "gaming":
        ent_raw += 0.25 * count_cues(text, L.GAMING_EVENTS) + (0.4 if visual and visual.motion_peak_z > 4 else 0)
    if context and context.potential:
        ent_raw += 0.6 * context.potential.get("entertain", 0)
    if ev_strength.get("laughter", 0) > 0.5:
        reasons.append("risas")

    # 3. narrative ----------------------------------------------------------------
    nar = (story.score if story else 0.0) * 0.75
    nar += 0.08 * min(3, story.qa_pairs if story else 0)
    if context and context.has_beginning and context.has_ending:
        nar += 0.2
    if story and story.score >= 0.5:
        reasons.append("historia con estructura narrativa")

    # 4. emotion ------------------------------------------------------------------
    emo = 0.55 * (emotion.arousal if emotion else 0.0)
    emo += sum(0.18 * ev_strength.get(t, 0) for t in ("shout", "surprise", "laughter", "anger", "intense_talk", "tone_shift"))
    emo += 0.08 * ev_strength.get("dramatic_pause", 0)
    emo += 0.15 * min(1.0, burst / 4)
    if visual and visual.expression_change > 0.25:
        emo += 0.1
    if ev_types:
        reasons.append("reacción: " + ", ".join(sorted(set(ev_types) - {"tone_shift"}))[:80])

    # 5. flow & context -------------------------------------------------------------
    flow = 0.35
    if sentences:
        if not starts_with_anaphora(sentences[0].text, L.ANAPHORA_STARTERS):
            flow += 0.2
        if sentences[-1].text.rstrip()[-1:] in ".!?…":
            flow += 0.15
        fillers = count_cues(text, L.FILLERS) / nwords
        flow -= min(0.3, fillers * 3)
        gaps = [b.start - a.end for a, b in zip(sentences, sentences[1:])]
        dead = sum(g for g in gaps if g > 2.5) / max(1.0, duration)
        flow += 0.2 * (1 - min(1.0, dead * 3))
        flow += 0.1 * float(np.mean([w.p for w in words])) if words else 0
    if context and context.standalone:
        flow += 0.1

    # 6. shareability ----------------------------------------------------------------
    share = 0.0
    if context and context.standalone:
        share += 0.3
    if hook and set(hook.types) & {"controversy", "revelation", "value", "curiosity"}:
        share += 0.25
    share += 0.15 * min(2, count_cues(text, L.VALUE)) / 2
    share += 0.2 * min(1.0, burst / 4) + 0.2 * (chat.repetition if chat and chat.messages else 0)
    if context and context.potential:
        share += 0.25 * max(context.potential.get("inform", 0), context.potential.get("surprise", 0))
    if count_cues(text, L.HOOK["controversy"]) or count_cues(text, L.HOOK["revelation"]):
        share += 0.15
    if chat and chat.messages and burst > 2.5:
        reasons.append(f"el chat reaccionó (×{burst:.1f})")

    subs = {
        "hook": hook_s,
        "entertainment": _sat(ent_raw),
        "narrative": float(np.clip(nar, 0, 1)),
        "emotion": _sat(emo * 1.3),
        "flow": float(np.clip(flow, 0, 1)),
        "shareability": _sat(share * 1.4),
        "audio_quality": audio_quality(rms_db, words),
    }
    if context and context.llm_scores:  # blend semantic judgement with signal evidence
        for c, v in context.llm_scores.items():
            if c in subs:
                subs[c] = 0.45 * subs[c] + 0.55 * v
    return {k: round(float(v), 4) for k, v in subs.items()}, reasons


class HighlightRanker:
    def __init__(self, settings: dict) -> None:
        self.settings = settings

    def rank(self, subscores: dict[str, float], *, category: str, learned: dict[str, float] | None = None,
             coverage: dict[str, bool] | None = None, context_confidence: float = 0.5,
             reasons: list[str] | None = None) -> RankResult:
        weights = effective_weights(self.settings["weights"], category, self.settings.get("context_adaptive", True), learned)
        points = {c: subscores.get(c, 0.0) * weights[c] for c in CRITERIA}
        score = float(sum(points.values()))
        cov = coverage or {}
        coverage_ratio = (sum(cov.values()) / len(cov)) if cov else 0.5
        conf = float(np.clip(0.35 * coverage_ratio + 0.4 * context_confidence + 0.25 * subscores.get("audio_quality", 0.5), 0, 1))
        return RankResult(round(score, 1), subscores, {k: round(v, 2) for k, v in points.items()},
                          {k: round(v, 2) for k, v in weights.items()},
                          priority_for(score, self.settings.get("tiers", {})), round(conf, 3), reasons or [])


def text_similarity(a: str, b: str) -> float:
    from clipper.analysis.text import jaccard, shingles

    return jaccard(shingles(a), shingles(b))


def temporal_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


norm  # used by importers
