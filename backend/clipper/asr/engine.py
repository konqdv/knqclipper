"""faster-whisper engine shared by all streams (one model in memory, bounded worker threads)."""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from clipper.core.system import cuda_available

log = logging.getLogger(__name__)


@dataclass
class Word:
    w: str
    s: float
    e: float
    p: float = 1.0
    spk: str | None = None

    def to_dict(self) -> dict:
        d = {"w": self.w, "s": round(self.s, 3), "e": round(self.e, 3), "p": round(self.p, 3)}
        if self.spk:
            d["spk"] = self.spk
        return d


@dataclass
class ASRSegment:
    start: float
    end: float
    text: str
    words: list[Word]
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    speaker: str | None = None


@dataclass
class ASRResult:
    segments: list[ASRSegment]
    language: str | None
    language_prob: float = 0.0
    elapsed: float = 0.0
    audio_seconds: float = 0.0
    meta: dict = field(default_factory=dict)


class WhisperEngine:
    def __init__(self, model: str, device: str = "auto", compute_type: str = "auto", models_dir: Path | None = None,
                 workers: int = 1, cpu_threads: int = 0, beam_size: int | None = None) -> None:
        self.model_name = model
        self.device = ("cuda" if cuda_available() else "cpu") if device == "auto" else device
        self.compute_type = compute_type if compute_type != "auto" else ("float16" if self.device == "cuda" else "int8")
        self.models_dir = models_dir
        self.beam_size = beam_size or (5 if self.device == "cuda" else 1)
        self.cpu_threads = cpu_threads or max(1, (os.cpu_count() or 2) // max(1, workers))
        self._workers = workers
        self._model = None
        self._load_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="asr")
        self.inflight = 0
        self.total_audio = 0.0
        self.total_elapsed = 0.0

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure_model(self):
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    from faster_whisper import WhisperModel

                    t = time.time()
                    root = str(self.models_dir / "hf" / "hub") if self.models_dir else None
                    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
                    self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type,
                                               download_root=root, cpu_threads=self.cpu_threads, num_workers=self._workers)
                    log.info("whisper %s loaded on %s/%s in %.1fs", self.model_name, self.device, self.compute_type, time.time() - t)
        return self._model

    def transcribe_sync(self, pcm: np.ndarray, language: str | None = None, prompt: str | None = None,
                        vad: bool = True) -> ASRResult:
        model = self._ensure_model()
        t = time.time()
        segments, info = model.transcribe(
            pcm, language=language, initial_prompt=prompt or None, word_timestamps=True,
            beam_size=self.beam_size, vad_filter=vad, vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False, temperature=[0.0, 0.2, 0.4],
        )
        out: list[ASRSegment] = []
        for seg in segments:
            words = [Word(w.word.strip(), float(w.start), float(w.end), float(w.probability)) for w in (seg.words or []) if w.word.strip()]
            out.append(ASRSegment(float(seg.start), float(seg.end), seg.text.strip(), words,
                                  float(seg.avg_logprob), float(seg.no_speech_prob)))
        elapsed = time.time() - t
        dur = len(pcm) / 16000
        self.total_audio += dur
        self.total_elapsed += elapsed
        return ASRResult(out, info.language, float(info.language_probability), elapsed, dur)

    def submit(self, pcm: np.ndarray, language: str | None = None, prompt: str | None = None) -> Future[ASRResult]:
        self.inflight += 1

        def run():
            try:
                return self.transcribe_sync(pcm, language, prompt)
            finally:
                self.inflight -= 1

        return self._pool.submit(run)

    @property
    def realtime_factor(self) -> float | None:
        return self.total_elapsed / self.total_audio if self.total_audio else None

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
