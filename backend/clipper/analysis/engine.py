"""Universal Highlight Engine.

Runs continuously per stream:

* every tick, fast triggers (vocal arousal/events, chat bursts, visual changes, salient sentences)
  are fused with a noisy-OR; above the sensitivity threshold a *candidate* is opened (and the
  rolling buffer is pinned so its media cannot be evicted);
* every ``scan_interval_s`` a *semantic scan* candidate is opened even without peaks, so calm
  content (education, podcasts) is evaluated on what is said rather than on how loud it is;
* once the post-roll has been captured *and transcribed*, the candidate is evaluated: story/hook
  detection, context analysis (LLM optional), natural boundaries, ranking, learned adjustment,
  deduplication — and handed to the session to persist and extract.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from clipper.analysis import lexicon as L
from clipper.analysis.chat import ChatAnalyzer
from clipper.analysis.classifier import ContentClassifier
from clipper.analysis.context import ContextAnalyzer
from clipper.analysis.emotion import EmotionDetector, filter_laughter_by_words
from clipper.analysis.hook import HookDetector, score_sentence
from clipper.analysis.ranker import HighlightRanker, compute_subscores, priority_for, temporal_iou, text_similarity
from clipper.analysis.story import StoryDetector
from clipper.analysis.text import Sentence, count_cues, segment_sentences
from clipper.analysis.visual import VisualAnalyzer
from clipper.asr.streaming import Transcript
from clipper.audio.features import FeatureTrack
from clipper.buffer.rolling import RollingBuffer
from clipper.clips.boundaries import choose_sentence_bounds, nonspeech_bounds, snap_times
from clipper.learning.features import build_features

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    t_first: float
    t_last: float
    peak_t: float
    kind: str  # trigger | scan
    strength: float
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, float] = field(default_factory=dict)
    opened_wall: float = field(default_factory=time.time)
    pin: int | None = None


@dataclass
class MomentResult:
    start: float
    end: float
    trigger_time: float
    kind: str
    score: float
    subscores: dict[str, float]
    points: dict[str, float]
    weights: dict[str, float]
    priority: str
    confidence: float
    category: str
    secondary: list[str]
    moment_types: list[str]
    reasons: list[str]
    title: str
    hook: str | None
    transcript: str
    words: list[dict]
    context: dict[str, Any]
    signals: dict[str, Any]
    model_info: dict[str, Any]

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class StreamAnalysis:
    buffer: RollingBuffer
    track: FeatureTrack
    emotion: EmotionDetector
    transcript: Transcript
    chat: ChatAnalyzer
    visual: VisualAnalyzer
    classifier: ContentClassifier
    has_asr: bool = True


def merge_reasons(old: list[str], new: list[str]) -> list[str]:
    """Union of reasons; lexical cues ("frase llamativa (a, b)") are collapsed into one line."""
    plain: list[str] = []
    lex: list[str] = []
    for r in old + new:
        if r.startswith("frase llamativa (") or r.startswith("frases llamativas: "):
            body = r.split("(", 1)[1].rstrip(")") if r.startswith("frase llamativa (") else r.split(": ", 1)[1]
            lex += [t.strip() for t in body.split(",") if t.strip()]
        elif r not in plain:
            plain.append(r)
    if lex:
        plain.append("frases llamativas: " + ", ".join(dict.fromkeys(lex)))
    return plain


def _noisy_or(values: list[tuple[float, float]]) -> float:
    p = 1.0
    for v, w in values:
        p *= 1 - max(0.0, min(1.0, v)) * w
    return 1 - p


class HighlightEngine:
    def __init__(
        self, a: StreamAnalysis, *, channel_config: dict[str, Any], detection: Callable[[], dict], ranking: Callable[[], dict],
        context: ContextAnalyzer, learner, creator: str | None, platform_category: Callable[[], str | None],
        on_moment: Callable[[MomentResult], Awaitable[int | None]], frame_grabber: Callable[[float], Awaitable[bytes | None]] | None = None,
    ) -> None:
        self.a = a
        self.cfg = channel_config
        self._detection = detection
        self._ranking = ranking
        self.context = context
        self.learner = learner
        self.creator = creator
        self._platform_category = platform_category
        self.on_moment = on_moment
        self.frame_grabber = frame_grabber
        self.hooks = HookDetector()
        self.story = StoryDetector()
        self.open: list[Candidate] = []
        self.recent: list[dict[str, Any]] = []  # accepted moments for dedup
        self._last_scan = 0.0
        self._last_lex_t = 0.0
        self._last_classify = -20.0  # first classification after ~10 s of content
        self._eval_lock = asyncio.Lock()
        self.stats = {"candidates": 0, "evaluated": 0, "accepted": 0, "duplicates": 0, "discarded": 0, "last_trigger": 0.0}
        self.last_signal: dict[str, float] = {}

    # --- time helpers -------------------------------------------------------------------
    def analysis_time(self) -> float:
        return self.a.track.end_time

    def ready_time(self) -> float:
        t = self.a.track.end_time
        if self.a.has_asr:
            t = min(t, self.a.transcript.transcribed_until)
        return t

    def _threshold(self) -> float:
        sens = float(self.cfg.get("sensitivity", 0.5))
        return 0.72 - 0.35 * sens

    # --- fast triggers -----------------------------------------------------------------
    def _lexical_trigger(self) -> tuple[float, float | None, list[str]]:
        until = self.a.transcript.transcribed_until
        if until <= self._last_lex_t:
            return 0.0, None, []
        words = self.a.transcript.window(self._last_lex_t, until)
        self._last_lex_t = until
        best, best_t, reasons = 0.0, None, []
        for s in segment_sentences(words):
            h = score_sentence(s)
            val = h.score
            if count_cues(s.text, L.STORY["intro"]):
                val = max(val, 0.55)
            if val > best:
                best, best_t = val, s.end
                reasons = [f"frase llamativa ({', '.join(h.types)})"] if h.types else []
        return best, best_t, reasons

    def fast_signal(self, now: float) -> tuple[float, float, list[str], dict[str, float]]:
        t0 = max(0.0, now - 3.0)
        emo, emo_r = self.a.emotion.quick_trigger(t0, now)
        chat, chat_r = self.a.chat.quick_trigger(t0, now) if self.cfg.get("chat_enabled", True) else (0.0, [])
        vis, vis_r = self.a.visual.quick_trigger(t0, now)
        lex, lex_t, lex_r = self._lexical_trigger()
        sig = {"emotion": round(emo, 3), "chat": round(chat, 3), "visual": round(vis, 3), "lexical": round(lex, 3)}
        val = _noisy_or([(emo, 0.9), (chat, 0.75), (vis, 0.5), (lex, 0.8)])
        sig["combined"] = round(val, 3)
        peak_t = now - 1.0
        if lex >= max(emo, chat, vis) and lex_t is not None:
            peak_t = lex_t
        return val, peak_t, emo_r + chat_r + vis_r + lex_r, sig

    # --- main loop step ----------------------------------------------------------------------
    async def tick(self) -> None:
        now = self.analysis_time()
        if now <= 0:
            return
        det = self._detection()
        val, peak_t, reasons, sig = self.fast_signal(now)
        self.last_signal = {**sig, "t": round(now, 2)}
        if val >= self._threshold():
            self._open_or_merge(peak_t, "trigger", val, reasons, sig, det)
        if now - self._last_scan >= float(det.get("scan_interval_s", 40)) and self.a.has_asr:
            self._last_scan = now
            post = float(self.cfg.get("post_seconds", 12))
            t = self.ready_time() - 1.0
            if t > post and not any(c.t_first - 5 <= t <= c.t_last + post for c in self.open):
                self._open_or_merge(t, "scan", 0.0, ["escaneo semántico periódico"], sig, det)
        if now - self._last_classify >= 30:
            self._last_classify = now
            text = self.a.transcript.text(max(0.0, now - 300), now)
            vs = self.a.visual.summarize(max(0.0, now - 120), now)
            self.a.classifier.set_platform_category(self._platform_category())
            await asyncio.to_thread(self.a.classifier.update, text, vs)
        await self._finalize_ready()

    def _open_or_merge(self, t: float, kind: str, strength: float, reasons: list[str], sig: dict, det: dict) -> None:
        post = float(self.cfg.get("post_seconds", 12))
        max_d = float(self.cfg.get("max_duration") or det.get("max_duration", 90))
        for c in self.open:
            if c.t_first - 5 <= t <= c.t_last + post and t - c.t_first < max_d:
                c.t_last = max(c.t_last, t)
                if strength > c.strength:
                    c.strength, c.peak_t = strength, t
                c.reasons = merge_reasons(c.reasons, reasons)
                for k, v in sig.items():
                    c.signals[k] = max(c.signals.get(k, 0.0), v)
                if kind == "trigger":
                    c.kind = "trigger"
                return
        pre = float(self.cfg.get("pre_seconds", 45))
        c = Candidate(t, t, t, kind, strength, merge_reasons([], reasons), dict(sig))
        c.pin = self.a.buffer.pin(max(0.0, t - pre - 10))
        self.open.append(c)
        self.stats["candidates"] += 1
        if kind == "trigger":
            self.stats["last_trigger"] = t

    async def _finalize_ready(self) -> None:
        post = float(self.cfg.get("post_seconds", 12))
        ready_t = self.ready_time()
        for c in list(self.open):
            timed_out = time.time() - c.opened_wall > post + 150
            if ready_t >= c.t_last + post or timed_out:
                self.open.remove(c)
                asyncio.create_task(self._evaluate_safe(c))

    async def flush(self) -> None:
        """Evaluate every open candidate now (stream ended)."""
        for c in list(self.open):
            self.open.remove(c)
            await self._evaluate_safe(c)

    async def _evaluate_safe(self, c: Candidate) -> None:
        try:
            async with self._eval_lock:
                await self.evaluate(c)
        except Exception:
            log.exception("candidate evaluation failed")
        finally:
            if c.pin is not None:
                self.a.buffer.unpin(c.pin)

    # --- evaluation ----------------------------------------------------------------------
    def _arousal_at(self, t0: float, t1: float) -> float:
        _, ar = self.a.emotion.arousal_series(t0, t1)
        return float(ar.max()) if len(ar) else 0.0

    async def evaluate(self, c: Candidate) -> MomentResult | None:
        det = self._detection()
        rank_cfg = self._ranking()
        self.stats["evaluated"] += 1
        pre, post = float(self.cfg.get("pre_seconds", 45)), float(self.cfg.get("post_seconds", 12))
        min_d = float(self.cfg.get("min_duration") or det.get("min_duration", 15))
        max_d = float(self.cfg.get("max_duration") or det.get("max_duration", 90))
        w0 = max(self.a.buffer.start_time, c.t_first - pre)
        w1 = min(self.a.buffer.end_time, c.t_last + post)
        if w1 - w0 < 3:
            self.stats["discarded"] += 1
            return None
        words = self.a.transcript.window(w0, w1)
        sentences = segment_sentences(words)
        emo_w = filter_laughter_by_words(await asyncio.to_thread(self.a.emotion.summarize, w0, w1), words)
        chat_w = self.a.chat.summarize(w0, w1) if self.cfg.get("chat_enabled", True) else None
        vis_w = self.a.visual.summarize(w0, w1)
        cls = self.a.classifier.current or self.a.classifier.update("", vis_w)
        category = cls.primary
        strong = [e for e in emo_w.events if e.type in ("laughter", "shout", "surprise")]
        peak_times = [e.t for e in strong]
        reaction_end = max([e.t + e.duration for e in strong if c.peak_t - 2 <= e.t <= c.peak_t + post], default=None)
        story = self.story.detect(sentences, peak_times)
        pref = self.learner.duration_range(self.creator, category) if self.learner else None

        lang = self.a.transcript.language
        notes: list[str] = []
        if sentences:
            i0, i1, notes = choose_sentence_bounds(sentences, c.peak_t, min_d=min_d, max_d=max_d, pref=pref, story=story,
                                                   reaction_end=reaction_end, arousal_at=self._arousal_at)
            hook0 = self.hooks.best_opening(sentences[i0: i1 + 1], arousal_at=self._arousal_at)
            images = None
            if self.frame_grabber and getattr(self.context.llm, "vision", False):
                img = await self.frame_grabber(c.peak_t)
                images = [img] if img else None
            ctx = await self.context.analyze(
                sentences, i0, i1, emotion=emo_w, chat=chat_w, visual=vis_w, story=story, hook=hook0,
                classification=cls, reasons=c.reasons, lang=lang, platform_category=self._platform_category(),
                trigger=c.peak_t, images=images)
            if ctx.source == "llm":
                dur = sentences[ctx.best_end].end - sentences[ctx.best_start].start
                if min_d * 0.6 <= dur <= max_d:
                    i0, i1 = ctx.best_start, ctx.best_end
                else:
                    notes.append("llm bounds outside duration limits → heuristic bounds kept")
            start, end = snap_times(words, sentences, i0, i1, reaction_end=reaction_end, window=(w0, w1))
            clip_sents: list[Sentence] = sentences[i0: i1 + 1]
            clip_words = words[clip_sents[0].i0: clip_sents[-1].i1 + 1]
        else:
            start, end = nonspeech_bounds(c.peak_t, (w0, w1), min_d)
            clip_sents, clip_words = [], []
            ctx = self.context.heuristic([], 0, 0, emotion=emo_w, chat=chat_w, visual=vis_w, story=story, hook=None,
                                         classification=cls, reasons=c.reasons, lang=lang)
        if end - start < 3:
            self.stats["discarded"] += 1
            return None

        # --- scoring on the final clip window ---------------------------------------------
        emo = filter_laughter_by_words(await asyncio.to_thread(self.a.emotion.summarize, start, end), clip_words)
        chat = self.a.chat.summarize(start, end) if chat_w is not None else None
        vis = self.a.visual.summarize(start, end)
        hook = self.hooks.best_opening(clip_sents, arousal_at=self._arousal_at) if clip_sents else None
        story_c = self.story.detect(clip_sents, [e.t for e in emo.events if e.type in ("laughter", "shout", "surprise")])
        frames = self.a.track.window(start, end)
        duration = end - start
        subs, reasons = compute_subscores(sentences=clip_sents, words=clip_words, emotion=emo, chat=chat, visual=vis,
                                          story=story_c, hook=hook, context=ctx, category=category,
                                          rms_db=frames.rms_db, duration=duration)
        learned = self.learner.weight_multipliers(self.creator, category) if self.learner else {}
        coverage = {"transcript": bool(clip_words), "audio": len(frames.t) > 0, "visual": vis.frames > 0,
                    "chat": bool(chat and chat.messages), "llm": ctx.source == "llm"}
        ranker = HighlightRanker(rank_cfg)
        rank = ranker.rank(subs, category=category, learned=learned, coverage=coverage, context_confidence=ctx.confidence,
                           reasons=merge_reasons(c.reasons, reasons))
        moment_types = ctx.moment_types
        silence_ratio = float(np.mean(frames.rms_db < -45)) if len(frames.t) else 0.0
        stats = {
            "duration": duration, "words_per_second": len(clip_words) / max(1.0, duration), "n_sentences": len(clip_sents),
            "n_speakers": len({s.speaker for s in clip_sents if s.speaker}), "peak_arousal": emo.arousal,
            "mean_arousal": emo.mean_arousal,
            "laughter": max([e.strength for e in emo.events if e.type == "laughter"], default=0.0),
            "shout": max([e.strength for e in emo.events if e.type == "shout"], default=0.0),
            "surprise": max([e.strength for e in emo.events if e.type == "surprise"], default=0.0),
            "chat_burst_z": chat.burst_z if chat and chat.messages else 0.0,
            "chat_repetition": chat.repetition if chat and chat.messages else 0.0,
            "visual_motion_z": min(20.0, vis.motion_peak_z), "faces_mean": vis.faces_mean,
            "scene_cuts_per_min": vis.scene_cuts / max(1.0, duration / 60), "silence_ratio": silence_ratio,
            "hook_first3s": hook.score if hook else 0.0, "story_score": story_c.score,
            "asr_confidence": float(np.mean([w.p for w in clip_words])) if clip_words else 0.0,
            "llm_used": 1.0 if ctx.source == "llm" else 0.0,
        }
        features = build_features(subscores=subs, category=category, moment_types=moment_types, stats=stats)
        if self.learner:
            score, model_info = self.learner.adjust_score(rank.score, features, self.creator, category)
        else:
            score, model_info = rank.score, {"heuristic": rank.score}
        if ctx.source == "llm" and not ctx.worth_clipping:
            score = round(score * 0.8, 1)
            model_info["llm_veto"] = True
        tiers = rank_cfg.get("tiers", {})
        priority = priority_for(score, tiers)
        floor = float(det.get("min_candidate_score", 50)) if c.kind == "trigger" else float(tiers.get("review", 60))
        if score < floor:
            self.stats["discarded"] += 1
            log.info("candidate @%.1fs discarded (score %.1f < %.0f)", c.peak_t, score, floor)
            return None
        text = " ".join(w.w for w in clip_words)
        dup = self._duplicate_of(start, end, text, det)
        if dup is not None:
            if score <= dup["score"] + 5:
                self.stats["duplicates"] += 1
                log.info("candidate @%.1fs is a duplicate of moment %s", c.peak_t, dup.get("id"))
                return None
            model_info["supersedes"] = dup.get("id")
        result = MomentResult(
            start=start, end=end, trigger_time=c.peak_t, kind=c.kind, score=score, subscores=subs, points=rank.points,
            weights=rank.weights, priority=priority, confidence=rank.confidence, category=category,
            secondary=list(dict.fromkeys(cls.secondary + moment_types)), moment_types=moment_types,
            reasons=rank.reasons[:10], title=ctx.title, hook=ctx.hook_quote or (hook.text if hook and hook.score > 0.3 else None),
            transcript=text, words=[w.to_dict() for w in clip_words], context={**ctx.to_dict(), "notes": ctx.notes + notes,
                                                                                 "story": story_c.to_dict(),
                                                                                 "classification": cls.to_dict()},
            signals={"features": features, "stats": stats, "trigger": c.signals, "emotion": emo.to_dict(),
                     "chat": chat.to_dict() if chat else None, "visual": vis.to_dict()},
            model_info=model_info,
        )
        mid = await self.on_moment(result)
        self.stats["accepted"] += 1
        self.recent.append({"id": mid, "start": start, "end": end, "text": text, "score": score})
        self.recent = self.recent[-50:]
        return result

    def _duplicate_of(self, start: float, end: float, text: str, det: dict) -> dict | None:
        for r in reversed(self.recent):
            if temporal_iou((start, end), (r["start"], r["end"])) > float(det.get("dedup_iou", 0.5)):
                return r
            if text and r["text"] and text_similarity(text, r["text"]) > float(det.get("dedup_text_similarity", 0.6)):
                return r
        return None
