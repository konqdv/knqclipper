"""Training Library: analyse reference clips the user imports and labels.

Extracted per example: duration, transcript, hook (first seconds), narrative structure, pacing,
content type, reactions/emotions and editing characteristics (aspect ratio, cut rate, burned-in
captions). The features use the same vector as live moments so they can train the same model.
Examples are private quality references; nothing is republished or copied into generated clips.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from clipper.analysis.chat import ChatAnalyzer
from clipper.analysis.classifier import ContentClassifier
from clipper.analysis.context import ContextAnalyzer
from clipper.analysis.emotion import EmotionDetector, filter_laughter_by_words
from clipper.analysis.hook import HookDetector
from clipper.analysis.ranker import compute_subscores
from clipper.analysis.story import StoryDetector
from clipper.analysis.text import segment_sentences
from clipper.analysis.visual import VisualAnalyzer
from clipper.asr.engine import WhisperEngine
from clipper.asr.streaming import StreamingTranscriber, Transcript
from clipper.audio.decode import decode_audio, decode_frames, probe_video
from clipper.audio.features import FeatureTrack, compute_features
from clipper.learning.features import build_features
from clipper.reframe.faces import FaceDetector

log = logging.getLogger(__name__)


def burned_captions_score(frames: list[tuple[float, np.ndarray]]) -> float:
    """Share of frames with dense high-contrast text-like edges in a horizontal band (captions)."""
    import cv2

    if not frames:
        return 0.0
    hits = 0
    for _, img in frames:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h = g.shape[0]
        band = g[int(h * 0.35): int(h * 0.85)]
        edges = cv2.Canny(band, 120, 240)
        rows = edges.mean(axis=1) / 255
        if (rows > 0.12).sum() >= max(3, len(rows) // 40):
            hits += 1
    return hits / len(frames)


def analyze_example(path: str | Path, engine: WhisperEngine | None, models_dir: Path | None = None) -> dict[str, Any]:
    info = probe_video(path)
    dur = float(info["duration"] or 0)
    pcm = decode_audio(path) if info["has_audio"] else np.zeros(0, dtype=np.float32)
    track = FeatureTrack()
    step = 16000 * 2
    for i in range(0, len(pcm), step):
        track.add(compute_features(pcm[i: i + step], i / 16000))
    transcript = Transcript()
    if engine is not None and len(pcm):
        st = StreamingTranscriber(engine, transcript)
        for i in range(0, len(pcm), step):
            st.feed(pcm[i: i + step], i / 16000)
        st.flush()
        st.wait_idle(600)
    words = transcript.window(0, dur + 1)
    sentences = segment_sentences(words)
    emotion = filter_laughter_by_words(EmotionDetector(track).summarize(0, dur), words)
    frames = decode_frames(path, fps=2.0, width=320)
    visual = VisualAnalyzer(FaceDetector(models_dir))
    visual.process(frames, 0.0)
    vis = visual.summarize(0, dur + 1)
    cls = ContentClassifier().update(" ".join(w.w for w in words), vis)
    story = StoryDetector().detect(sentences, [e.t for e in emotion.events if e.type in ("laughter", "shout", "surprise")])
    hook = HookDetector().best_opening([s for s in sentences if s.start < 5.0] or sentences[:1])
    ctx = ContextAnalyzer(None).heuristic(sentences, 0, max(0, len(sentences) - 1), emotion=emotion, chat=None, visual=vis,
                                          story=story, hook=hook, classification=cls, reasons=[], lang=transcript.language)
    rms = track.window(0, dur + 1).rms_db
    subs, reasons = compute_subscores(sentences=sentences, words=words, emotion=emotion, chat=None, visual=vis, story=story,
                                      hook=hook, context=ctx, category=cls.primary, rms_db=rms, duration=max(1.0, dur))
    silence = float(np.mean(rms < -45)) if len(rms) else 0.0
    stats = {
        "duration": dur, "words_per_second": len(words) / max(1.0, dur), "n_sentences": len(sentences),
        "n_speakers": len({s.speaker for s in sentences if s.speaker}), "peak_arousal": emotion.arousal,
        "mean_arousal": emotion.mean_arousal,
        "laughter": max([e.strength for e in emotion.events if e.type == "laughter"], default=0.0),
        "shout": max([e.strength for e in emotion.events if e.type == "shout"], default=0.0),
        "surprise": max([e.strength for e in emotion.events if e.type == "surprise"], default=0.0),
        "chat_burst_z": 0.0, "chat_repetition": 0.0, "visual_motion_z": min(20.0, vis.motion_peak_z),
        "faces_mean": vis.faces_mean, "scene_cuts_per_min": vis.scene_cuts / max(1.0, dur / 60), "silence_ratio": silence,
        "hook_first3s": hook.score if hook else 0.0, "story_score": story.score,
        "asr_confidence": float(np.mean([w.p for w in words])) if words else 0.0, "llm_used": 0.0,
    }
    features = build_features(subscores=subs, category=cls.primary, moment_types=ctx.moment_types, stats=stats)
    editing = {
        "aspect_ratio": round((info["width"] or 1) / (info["height"] or 1), 3), "vertical": (info["height"] or 0) > (info["width"] or 0),
        "resolution": f"{info['width']}x{info['height']}", "fps": info["fps"],
        "scene_cuts_per_min": round(stats["scene_cuts_per_min"], 2), "burned_captions": round(burned_captions_score(frames[::4]), 2),
    }
    analysis = {
        "category": cls.primary, "secondary": cls.secondary, "language": transcript.language,
        "hook": {"text": hook.text if hook else None, "score": round(hook.score, 3) if hook else 0, "types": hook.types if hook else []},
        "narrative": story.to_dict(), "pacing": {"words_per_second": round(stats["words_per_second"], 2), "silence_ratio": round(silence, 3)},
        "emotions": {"peak_arousal": round(emotion.arousal, 3), "events": [e.type for e in emotion.events][:20]},
        "moment_types": ctx.moment_types, "editing": editing, "subscores": subs, "reasons": reasons,
    }
    return {"duration": dur, "transcript": " ".join(w.w for w in words), "analysis": analysis,
            "features": {"features": features, "stats": stats}}


ChatAnalyzer  # noqa: B018 (kept for parity with live analysis imports)
