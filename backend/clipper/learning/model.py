"""Feedback Learning System: lightweight supervised model + preferences.

* Model: L2-regularised logistic regression (scikit-learn) on *augmented* features — every feature
  appears as a global copy, a per-creator copy and a per-category copy (Daumé III, "Frustratingly
  Easy Domain Adaptation"). One small model therefore learns general taste *and* creator/category
  specific taste, falling back to the global part when a creator has little data.
* Validation: stratified k-fold AUC / log-loss / accuracy; a candidate model is promoted to
  production only if it beats the current one on the same data by ``promote_margin``.
* Ranker weight adjustment: coefficients of the sub-score features are mapped to bounded weight
  multipliers (0.5–1.5) per criterion.
* Duration preferences: weighted statistics of approved vs rejected durations per creator × category.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from clipper.analysis.ranker import CRITERIA

log = logging.getLogger(__name__)


@dataclass
class Example:
    features: dict[str, float]
    label: int  # 1 good, 0 bad
    weight: float
    creator: str | None
    category: str | None
    duration: float
    ts: float = 0.0


def label_from_feedback(decision: str, rating: int | None, mediocre: bool) -> tuple[int, float] | None:
    """Map a feedback record to (label, sample weight). ``None`` = not informative."""
    if rating is not None:
        if rating >= 4:
            return 1, 1.0 if rating == 4 else 1.4
        if rating <= 2:
            return 0, 1.0 if rating == 2 else 1.4
        return 0, 0.4  # 3/5 "normal" leans negative but weakly
    if mediocre:
        return 0, 0.6
    if decision == "approve":
        return 1, 1.0
    if decision == "reject":
        return 0, 1.0
    return None


TRAINING_LABELS = {"excellent": (1, 1.5), "good": (1, 1.0), "normal": (0, 0.4), "bad": (0, 1.0), "unpublishable": (0, 1.5)}


def augment(features: dict[str, float], creator: str | None, category: str | None) -> dict[str, float]:
    out = {f"g:{k}": v for k, v in features.items()}
    if creator:
        out.update({f"c:{creator}:{k}": v for k, v in features.items() if k.startswith("sub_") or k.startswith("dur_") or k.startswith("type_")})
    if category:
        out.update({f"k:{category}:{k}": v for k, v in features.items() if k.startswith("sub_") or k.startswith("dur_") or k.startswith("type_")})
    return out


class FeedbackModel:
    """A trained (or empty) model that can score feature dicts."""

    def __init__(self, params: dict[str, Any] | None = None, version: str | None = None) -> None:
        self.params = params or {}
        self.version = version
        self._names: list[str] = self.params.get("names", [])
        self._coef = np.array(self.params.get("coef", []), dtype=float)
        self._intercept = float(self.params.get("intercept", 0.0))
        self._mean = np.array(self.params.get("mean", []), dtype=float)
        self._scale = np.array(self.params.get("scale", []), dtype=float)
        self._index = {n: i for i, n in enumerate(self._names)}
        self.n_samples = int(self.params.get("n_samples", 0))

    @property
    def ready(self) -> bool:
        return len(self._coef) > 0

    def _vector(self, aug: dict[str, float]) -> np.ndarray:
        x = np.zeros(len(self._names))
        for k, v in aug.items():
            i = self._index.get(k)
            if i is not None:
                x[i] = v
        return (x - self._mean) / self._scale

    def predict_proba(self, features: dict[str, float], creator: str | None, category: str | None) -> float | None:
        if not self.ready:
            return None
        z = float(self._vector(augment(features, creator, category)) @ self._coef + self._intercept)
        return 1 / (1 + math.exp(-max(-30, min(30, z))))

    def weight_multipliers(self, creator: str | None = None, category: str | None = None) -> dict[str, float]:
        """Map sub-score coefficients to multipliers for the ranker weights (bounded)."""
        if not self.ready:
            return {}
        out = {}
        for c in CRITERIA:
            coef = 0.0
            for key in (f"g:sub_{c}", f"c:{creator}:sub_{c}" if creator else None, f"k:{category}:sub_{c}" if category else None):
                if key and key in self._index:
                    coef += self._coef[self._index[key]] / max(1e-6, self._scale[self._index[key]])
            out[c] = float(np.clip(1.0 + 0.25 * np.tanh(coef / 4), 0.5, 1.5))
        return out


def _train(examples: list[Example], C: float = 0.5):
    from sklearn.linear_model import LogisticRegression

    names = sorted({k for e in examples for k in augment(e.features, e.creator, e.category)})
    index = {n: i for i, n in enumerate(names)}
    X = np.zeros((len(examples), len(names)))
    for r, e in enumerate(examples):
        for k, v in augment(e.features, e.creator, e.category).items():
            X[r, index[k]] = v
    y = np.array([e.label for e in examples])
    w = np.array([e.weight for e in examples])
    mean = X.mean(0)
    scale = X.std(0)
    scale[scale < 1e-6] = 1.0
    Xs = (X - mean) / scale
    clf = LogisticRegression(C=C, max_iter=2000, class_weight="balanced")
    clf.fit(Xs, y, sample_weight=w)
    return clf, names, mean, scale, Xs, y, w


def cross_validate(examples: list[Example], folds: int = 5, C: float = 0.5) -> dict[str, float]:
    from sklearn.metrics import accuracy_score, log_loss, roc_auc_score
    from sklearn.model_selection import StratifiedKFold

    y = np.array([e.label for e in examples])
    k = int(min(folds, np.bincount(y, minlength=2).min()))
    if k < 2:
        return {"auc": float("nan"), "log_loss": float("nan"), "accuracy": float("nan"), "folds": 0}
    probs = np.zeros(len(examples))
    skf = StratifiedKFold(n_splits=k, shuffle=True, random_state=7)
    for tr, te in skf.split(np.zeros(len(y)), y):
        clf, names, mean, scale, *_ = _train([examples[i] for i in tr], C)
        m = FeedbackModel({"names": names, "coef": clf.coef_[0].tolist(), "intercept": float(clf.intercept_[0]),
                           "mean": mean.tolist(), "scale": scale.tolist()})
        for i in te:
            probs[i] = m.predict_proba(examples[i].features, examples[i].creator, examples[i].category)
    w = np.array([e.weight for e in examples])
    return {
        "auc": float(roc_auc_score(y, probs, sample_weight=w)),
        "log_loss": float(log_loss(y, np.clip(probs, 1e-6, 1 - 1e-6), sample_weight=w)),
        "accuracy": float(accuracy_score(y, probs >= 0.5, sample_weight=w)),
        "folds": k,
    }


def evaluate_model(model: FeedbackModel, examples: list[Example]) -> dict[str, float]:
    from sklearn.metrics import roc_auc_score

    if not model.ready:
        return {"auc": 0.5}
    y = np.array([e.label for e in examples])
    if len(set(y)) < 2:
        return {"auc": float("nan")}
    p = np.array([model.predict_proba(e.features, e.creator, e.category) for e in examples])
    return {"auc": float(roc_auc_score(y, p, sample_weight=[e.weight for e in examples]))}


def fit_model(examples: list[Example], C: float = 0.5) -> tuple[dict[str, Any], dict[str, float]]:
    metrics = cross_validate(examples, C=C)
    clf, names, mean, scale, Xs, y, w = _train(examples, C)
    params = {"names": names, "coef": clf.coef_[0].tolist(), "intercept": float(clf.intercept_[0]),
              "mean": mean.tolist(), "scale": scale.tolist(), "n_samples": len(examples),
              "positives": int(y.sum()), "trained_at": time.time()}
    # top global features for the "patterns" report
    g = [(n, c) for n, c in zip(names, clf.coef_[0]) if n.startswith("g:")]
    g.sort(key=lambda kv: -abs(kv[1]))
    params["top_features"] = [{"feature": n[2:], "coef": round(float(c), 3)} for n, c in g[:15]]
    return params, metrics


class DurationPreferences:
    """Preferred duration range per (creator, category) learned from approved/rejected clips."""

    def __init__(self, table: dict[str, dict[str, float]] | None = None) -> None:
        self.table = table or {}

    @staticmethod
    def key(creator: str | None, category: str | None) -> str:
        return f"{creator or '*'}|{category or '*'}"

    @classmethod
    def fit(cls, examples: list[Example], min_n: int = 5) -> "DurationPreferences":
        groups: dict[str, list[Example]] = {}
        for e in examples:
            if e.label != 1 or e.duration <= 0:
                continue
            for k in (cls.key(e.creator, e.category), cls.key(e.creator, None), cls.key(None, e.category), cls.key(None, None)):
                groups.setdefault(k, []).append(e)
        table = {}
        for k, items in groups.items():
            if len(items) < min_n:
                continue
            d = np.array([i.duration for i in items])
            w = np.array([i.weight for i in items])
            order = np.argsort(d)
            cw = np.cumsum(w[order]) / w.sum()
            lo = float(d[order][np.searchsorted(cw, 0.2)])
            hi = float(d[order][min(len(d) - 1, np.searchsorted(cw, 0.8))])
            table[k] = {"lo": round(lo, 1), "hi": round(max(hi, lo + 5), 1), "n": len(items),
                        "median": round(float(np.median(d)), 1)}
        return cls(table)

    def range(self, creator: str | None, category: str | None) -> tuple[float, float] | None:
        for k in (self.key(creator, category), self.key(creator, None), self.key(None, category), self.key(None, None)):
            if k in self.table:
                return self.table[k]["lo"], self.table[k]["hi"]
        return None
