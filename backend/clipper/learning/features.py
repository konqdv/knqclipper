"""Feature vectors shared by the ranker, the feedback model and the training library."""

from __future__ import annotations

from typing import Any

from clipper.analysis.lexicon import CATEGORIES
from clipper.analysis.ranker import CRITERIA

MOMENT_TYPES = ["humor", "reaction", "story", "revelation", "controversy", "opinion", "conflict", "tension",
                "useful_tip", "surprising_fact", "play", "fail", "interaction", "question", "conversation"]

NUMERIC = [
    "duration", "words_per_second", "n_sentences", "n_speakers", "peak_arousal", "mean_arousal",
    "laughter", "shout", "surprise", "chat_burst_z", "chat_repetition", "visual_motion_z", "faces_mean",
    "scene_cuts_per_min", "silence_ratio", "hook_first3s", "story_score", "asr_confidence", "llm_used",
]


def build_features(*, subscores: dict[str, float], category: str, moment_types: list[str],
                   stats: dict[str, Any]) -> dict[str, float]:
    f: dict[str, float] = {}
    for c in CRITERIA:
        f[f"sub_{c}"] = float(subscores.get(c, 0.0))
    for k in NUMERIC:
        v = stats.get(k, 0.0)
        f[k] = float(v) if v is not None else 0.0
    for c in CATEGORIES:
        f[f"cat_{c}"] = 1.0 if category == c else 0.0
    for t in MOMENT_TYPES:
        f[f"type_{t}"] = 1.0 if t in moment_types else 0.0
    # scaled duration buckets help a linear model learn non-monotonic duration preferences
    d = f["duration"]
    for lo, hi in ((0, 20), (20, 35), (35, 50), (50, 75), (75, 1e9)):
        f[f"dur_{lo}_{int(min(hi, 999))}"] = 1.0 if lo <= d < hi else 0.0
    return f
