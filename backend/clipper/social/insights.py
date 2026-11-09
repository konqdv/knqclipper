"""Social Performance Analyzer: publication metrics → insights.

Keeps *editorial quality* (user ratings) and *distribution performance* (platform metrics) as two
separate dimensions, and reports where they disagree instead of assuming views = good editing.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import numpy as np
from sqlalchemy import select

from clipper.db.models import Clip, Feedback, MetricSnapshot, Moment, Publication
from clipper.db.session import session_scope
from clipper.learning.service import performance_index

PLATFORMS = ("tiktok", "reels", "shorts")
METRIC_FIELDS = ("views", "avg_retention", "completion_rate", "watch_time_s", "likes", "shares", "comments", "saves", "followers_gained")


def _bucket_duration(d: float) -> str:
    for lo, hi in ((0, 20), (20, 35), (35, 50), (50, 75)):
        if lo <= d < hi:
            return f"{lo}-{hi}s"
    return "75s+"


def _bucket_hook(h: float) -> str:
    return "fuerte" if h >= 0.6 else ("medio" if h >= 0.3 else "débil")


def _bucket_pace(wps: float) -> str:
    return "lento (<2 p/s)" if wps < 2 else ("medio (2-3 p/s)" if wps < 3 else "rápido (>3 p/s)")


def parse_csv(text: str) -> list[dict[str, Any]]:
    """CSV columns: clip_id, platform, url, posted_at, views, avg_retention, completion_rate, watch_time_s,
    likes, shares, comments, saves, followers_gained (header names are case-insensitive)."""
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        r = {k.strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k}
        if not r.get("clip_id") or not r.get("platform"):
            continue
        out: dict[str, Any] = {"clip_id": int(r["clip_id"]), "platform": r["platform"].lower(), "url": r.get("url") or None}
        for f in METRIC_FIELDS:
            v = r.get(f)
            if v not in (None, ""):
                out[f] = float(v.replace("%", "").replace(",", "")) if f in ("avg_retention", "completion_rate", "watch_time_s") else int(float(v.replace(",", "")))
        rows.append(out)
    return rows


async def build_insights() -> dict[str, Any]:
    async with session_scope() as s:
        pubs = (await s.execute(select(Publication))).scalars().all()
        snaps = (await s.execute(select(MetricSnapshot).order_by(MetricSnapshot.captured_at))).scalars().all()
        latest = {m.publication_id: m for m in snaps}
        ratings: dict[int, int] = {}
        for fb in (await s.execute(select(Feedback).where(Feedback.rating.is_not(None)))).scalars().all():
            if fb.clip_id:
                ratings[fb.clip_id] = fb.rating  # type: ignore[assignment]
            if fb.moment_id:
                ratings.setdefault(-fb.moment_id, fb.rating)  # type: ignore[arg-type]
        rows = []
        for p in pubs:
            m = latest.get(p.id)
            c = await s.get(Clip, p.clip_id)
            if not m or not c:
                continue
            mom = await s.get(Moment, c.moment_id) if c.moment_id else None
            feats = (mom.signals or {}).get("features", {}) if mom else {}
            rows.append({
                "platform": p.platform, "clip_id": c.id, "title": c.title, "views": m.views or 0,
                "retention": m.avg_retention, "completion": m.completion_rate,
                "engagement": ((m.likes or 0) + (m.shares or 0) * 2 + (m.comments or 0) + (m.saves or 0) * 2) / max(1, m.views or 0),
                "perf": performance_index(m), "duration": c.duration or (mom.end - mom.start if mom else 0),
                "category": mom.category if mom else "unknown", "caption_style": (c.edit.get("caption_style") or {}).get("style", "?"),
                "hook": feats.get("sub_hook", 0.0), "pace": feats.get("words_per_second", 0.0),
                "types": [k[5:] for k, v in feats.items() if k.startswith("type_") and v],
                "rating": ratings.get(c.id) or (ratings.get(-mom.id) if mom else None),
            })

    def group(key) -> list[dict[str, Any]]:
        g: dict[str, list[dict]] = {}
        for r in rows:
            ks = key(r)
            for k in ks if isinstance(ks, list) else [ks]:
                g.setdefault(k, []).append(r)
        out = []
        for k, items in g.items():
            def avg(f):
                vals = [i[f] for i in items if i[f] is not None]
                return round(float(np.mean(vals)), 3) if vals else None
            out.append({"group": k, "n": len(items), "avg_views": avg("views"), "avg_retention": avg("retention"),
                        "avg_completion": avg("completion"), "engagement": avg("engagement"), "performance": avg("perf")})
        return sorted(out, key=lambda r: -(r["performance"] or 0))

    disagreements = []
    rated = [r for r in rows if r["rating"] is not None and r["perf"] is not None]
    corr = None
    if len(rated) >= 5:
        perf_med = float(np.median([r["perf"] for r in rated]))
        for r in rated:
            if r["rating"] >= 4 and r["perf"] < perf_med * 0.7:
                disagreements.append({**r, "kind": "buena edición, bajo rendimiento"})
            elif r["rating"] <= 2 and r["perf"] > perf_med * 1.3:
                disagreements.append({**r, "kind": "rendimiento alto pese a baja calidad editorial"})
        corr = float(np.corrcoef([r["rating"] for r in rated], [r["perf"] for r in rated])[0, 1])
    return {
        "publications": len(rows),
        "by_duration": group(lambda r: _bucket_duration(r["duration"])),
        "by_category": group(lambda r: r["category"]),
        "by_caption_style": group(lambda r: r["caption_style"]),
        "by_hook_strength": group(lambda r: _bucket_hook(r["hook"])),
        "by_pacing": group(lambda r: _bucket_pace(r["pace"])),
        "by_moment_type": group(lambda r: r["types"] or ["conversation"]),
        "by_platform": group(lambda r: r["platform"]),
        "editorial_vs_performance": {"rated": len(rated), "correlation": None if corr is None or np.isnan(corr) else round(corr, 3),
                                     "disagreements": disagreements[:20]},
        "note": "Las métricas de publicación miden distribución, no calidad de edición; se analizan por separado.",
    }
