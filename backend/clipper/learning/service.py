"""Learner service: keeps the production model in memory and manages training/validation/versions."""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
from sqlalchemy import select

from clipper.core.appsettings import store
from clipper.core.events import bus
from clipper.db.models import Channel, Clip, Feedback, MetricSnapshot, Moment, ModelVersion, Publication, TrainingExample
from clipper.db.session import session_scope
from clipper.learning.model import (
    TRAINING_LABELS, DurationPreferences, Example, FeedbackModel, evaluate_model, fit_model, label_from_feedback,
)

log = logging.getLogger(__name__)


class Learner:
    def __init__(self) -> None:
        self.model = FeedbackModel()
        self.prefs = DurationPreferences()
        self.performance_model = FeedbackModel()
        self.version: str | None = None

    # --- inference -------------------------------------------------------------------
    def blend_alpha(self) -> float:
        cfg = store.get("learning")
        n = self.model.n_samples
        if not self.model.ready or n < cfg.get("min_samples", 20):
            return 0.0
        return float(n / (n + cfg.get("blend_k", 40)))

    def adjust_score(self, heuristic: float, features: dict[str, float], creator: str | None, category: str | None) -> tuple[float, dict[str, Any]]:
        info: dict[str, Any] = {"heuristic": heuristic, "model_version": self.version}
        p = self.model.predict_proba(features, creator, category)
        alpha = self.blend_alpha()
        score = heuristic
        if p is not None and alpha > 0:
            score = (1 - alpha) * heuristic + alpha * 100 * p
            info.update(model_prob=round(p, 3), alpha=round(alpha, 3))
        perf = self.performance_model.predict_proba(features, creator, category)
        if perf is not None:
            w = float(store.get("learning").get("performance_weight", 0.15))
            score = (1 - w) * score + w * 100 * perf
            info["performance_prob"] = round(perf, 3)
        return round(float(np.clip(score, 0, 100)), 1), info

    def weight_multipliers(self, creator: str | None, category: str | None) -> dict[str, float]:
        return self.model.weight_multipliers(creator, category) if self.blend_alpha() > 0 else {}

    def duration_range(self, creator: str | None, category: str | None) -> tuple[float, float] | None:
        return self.prefs.range(creator, category)

    # --- persistence -------------------------------------------------------------------
    async def load(self) -> None:
        async with session_scope() as s:
            for kind in ("editorial", "performance"):
                row = (await s.execute(select(ModelVersion).where(ModelVersion.kind == kind, ModelVersion.status == "production")
                                       .order_by(ModelVersion.id.desc()))).scalars().first()
                if row is None:
                    continue
                m = FeedbackModel(row.params, row.version)
                if kind == "editorial":
                    self.model, self.version = m, row.version
                    self.prefs = DurationPreferences(row.params.get("duration_prefs"))
                else:
                    self.performance_model = m

    # --- dataset ---------------------------------------------------------------------
    async def editorial_examples(self) -> list[Example]:
        out: list[Example] = []
        async with session_scope() as s:
            rows = (await s.execute(select(Feedback).order_by(Feedback.created_at))).scalars().all()
            channels = {c.id: c.handle for c in (await s.execute(select(Channel))).scalars().all()}
            latest: dict[tuple, Feedback] = {}
            for fb in rows:  # last feedback per moment/clip wins
                latest[(fb.moment_id, fb.clip_id if fb.moment_id is None else None)] = fb
            for fb in latest.values():
                lab = label_from_feedback(fb.decision, fb.rating, fb.mediocre)
                snap = fb.snapshot or {}
                feats = (snap.get("signals") or {}).get("features")
                if lab is None or not feats:
                    continue
                if fb.needs_recut and lab[0] == 1:
                    lab = (1, lab[1] * 0.7)  # good moment, imperfect boundaries
                out.append(Example(feats, lab[0], lab[1], channels.get(fb.channel_id), snap.get("category"),
                                   float(snap.get("duration") or feats.get("duration", 0)), fb.created_at))
            for te in (await s.execute(select(TrainingExample).where(TrainingExample.status == "ready"))).scalars().all():
                lab = TRAINING_LABELS.get(te.label)
                feats = (te.features or {}).get("features")
                if lab is None or not feats:
                    continue
                out.append(Example(feats, lab[0], lab[1] * 0.8, te.creator, te.analysis.get("category"), te.duration, te.created_at))
        return out

    async def performance_examples(self) -> list[Example]:
        """Label = clip outperformed the median of its platform (views-normalised retention)."""
        out: list[Example] = []
        async with session_scope() as s:
            pubs = (await s.execute(select(Publication))).scalars().all()
            latest: dict[int, MetricSnapshot] = {}
            for m in (await s.execute(select(MetricSnapshot).order_by(MetricSnapshot.captured_at))).scalars().all():
                latest[m.publication_id] = m
            rows = []
            for p in pubs:
                m = latest.get(p.id)
                clip = await s.get(Clip, p.clip_id)
                mom = await s.get(Moment, clip.moment_id) if clip and clip.moment_id else None
                if not m or not mom or not (mom.signals or {}).get("features"):
                    continue
                perf = performance_index(m)
                if perf is None:
                    continue
                rows.append((p.platform, perf, mom))
            by_platform: dict[str, list[float]] = {}
            for plat, perf, _ in rows:
                by_platform.setdefault(plat, []).append(perf)
            for plat, perf, mom in rows:
                med = float(np.median(by_platform[plat]))
                out.append(Example(mom.signals["features"], int(perf > med), 1.0, None, mom.category, mom.end - mom.start))
        return out

    # --- training / validation / promotion ------------------------------------------------
    async def train(self, kind: str = "editorial", auto_promote: bool | None = None) -> dict[str, Any]:
        examples = await (self.editorial_examples() if kind == "editorial" else self.performance_examples())
        cfg = store.get("learning")
        n_pos = sum(e.label for e in examples)
        if len(examples) < cfg.get("min_samples", 20) or n_pos == 0 or n_pos == len(examples):
            return {"status": "insufficient_data", "n": len(examples), "positives": n_pos,
                    "min_samples": cfg.get("min_samples", 20)}
        params, metrics = fit_model(examples)
        if kind == "editorial":
            params["duration_prefs"] = DurationPreferences.fit(examples).table
        current = self.model if kind == "editorial" else self.performance_model
        baseline = evaluate_model(current, examples) if current.ready else {"auc": 0.5}
        metrics["baseline_auc"] = baseline.get("auc")
        version = time.strftime("v%Y%m%d-%H%M%S")
        margin = float(cfg.get("promote_margin", 0.01))
        cand_auc = metrics.get("auc", float("nan"))
        base_auc = baseline.get("auc") if baseline.get("auc") == baseline.get("auc") else 0.5
        better = cand_auc == cand_auc and cand_auc >= (base_auc or 0.5) + margin
        promote = better and (cfg.get("auto_promote", True) if auto_promote is None else auto_promote)
        async with session_scope() as s:
            row = ModelVersion(kind=kind, version=version, status="candidate" if not promote else "production",
                               n_samples=len(examples), metrics=metrics, params=params,
                               notes=None if better else "did not beat production model in validation")
            if promote:
                for old in (await s.execute(select(ModelVersion).where(ModelVersion.kind == kind, ModelVersion.status == "production"))).scalars().all():
                    old.status = "archived"
            elif not better:
                row.status = "rejected"
            s.add(row)
            await s.flush()
            row_id = row.id
        if promote:
            await self.load()
        bus.publish("learning.trained", kind=kind, version=version, promoted=promote, metrics=metrics)
        return {"status": "promoted" if promote else ("candidate" if better else "rejected"), "version": version,
                "id": row_id, "metrics": metrics, "n": len(examples)}

    async def promote(self, model_id: int) -> None:
        async with session_scope() as s:
            row = await s.get(ModelVersion, model_id)
            if row is None:
                raise ValueError("model not found")
            for old in (await s.execute(select(ModelVersion).where(ModelVersion.kind == row.kind, ModelVersion.status == "production"))).scalars().all():
                old.status = "archived"
            row.status = "production"
        await self.load()

    async def patterns(self) -> dict[str, Any]:
        """What distinguishes approved from rejected moments (means of key features + reasons)."""
        examples = await self.editorial_examples()
        good = [e for e in examples if e.label == 1]
        bad = [e for e in examples if e.label == 0]
        keys = ["duration", "sub_hook", "sub_entertainment", "sub_narrative", "sub_emotion", "sub_flow",
                "sub_shareability", "words_per_second", "chat_burst_z", "story_score"]
        diff = []
        for k in keys:
            g = float(np.mean([e.features.get(k, 0) for e in good])) if good else None
            b = float(np.mean([e.features.get(k, 0) for e in bad])) if bad else None
            diff.append({"feature": k, "approved": None if g is None else round(g, 3), "rejected": None if b is None else round(b, 3)})
        reasons: dict[str, dict[str, int]] = {"approve": {}, "reject": {}}
        async with session_scope() as s:
            for fb in (await s.execute(select(Feedback))).scalars().all():
                bucket = reasons["approve" if fb.decision == "approve" or (fb.rating or 0) >= 4 else "reject"]
                for r in fb.reasons or []:
                    bucket[r] = bucket.get(r, 0) + 1
        return {"n_approved": len(good), "n_rejected": len(bad), "features": diff, "reasons": reasons,
                "duration_preferences": self.prefs.table, "model_version": self.version,
                "top_features": self.model.params.get("top_features", [])}


def performance_index(m: MetricSnapshot) -> float | None:
    """Platform-relative performance proxy combining retention/completion with engagement per view."""
    if not m.views:
        return None
    ret = (m.avg_retention or m.completion_rate or 0) / 100
    eng = ((m.shares or 0) * 3 + (m.saves or 0) * 2 + (m.comments or 0) + (m.likes or 0) * 0.5) / max(1, m.views)
    return float(0.6 * ret + 0.4 * min(1.0, eng * 20) + 0.05 * np.log10(max(1, m.views)))


learner = Learner()
