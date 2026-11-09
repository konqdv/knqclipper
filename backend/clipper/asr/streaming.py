"""Incremental transcription for live audio.

Audio arrives per segment; it is accumulated and cut at natural pauses (never mid-word when
avoidable), each chunk is transcribed asynchronously with word timestamps, and results are
applied strictly in order to a shared :class:`Transcript` in stream time.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future

import numpy as np

from clipper.asr.cleanup import dedupe_loops, is_hallucination, tidy_words
from clipper.asr.engine import ASRResult, ASRSegment, WhisperEngine, Word
from clipper.asr.speakers import SpeakerTracker

log = logging.getLogger(__name__)
SR = 16000


class Transcript:
    """Thread-safe store of words in stream time."""

    def __init__(self, horizon_s: float = 3600.0) -> None:
        self._lock = threading.Lock()
        self.words: list[Word] = []
        self.segments: list[ASRSegment] = []
        self.transcribed_until = 0.0
        self.language: str | None = None
        self.horizon_s = horizon_s

    def add(self, segments: list[ASRSegment], until: float) -> None:
        with self._lock:
            for s in segments:
                self.segments.append(s)
                self.words.extend(s.words)
            self.transcribed_until = max(self.transcribed_until, until)
            cutoff = self.transcribed_until - self.horizon_s
            if self.words and self.words[0].e < cutoff:
                self.words = [w for w in self.words if w.e >= cutoff]
                self.segments = [s for s in self.segments if s.end >= cutoff]

    def window(self, t0: float, t1: float) -> list[Word]:
        with self._lock:
            return [w for w in self.words if w.e > t0 and w.s < t1]

    def segments_between(self, t0: float, t1: float) -> list[ASRSegment]:
        with self._lock:
            return [s for s in self.segments if s.end > t0 and s.start < t1]

    def text(self, t0: float, t1: float) -> str:
        return " ".join(w.w for w in self.window(t0, t1))

    def tail_text(self, chars: int = 220) -> str:
        with self._lock:
            txt = " ".join(w.w for w in self.words[-60:])
        return txt[-chars:]


def find_cut(pcm: np.ndarray, min_s: float, max_s: float, sr: int = SR) -> int | None:
    """Sample index to cut at: middle of the latest pause after ``min_s``; forced near ``max_s``."""
    hop = int(0.02 * sr)
    n = len(pcm) // hop
    if n * hop < min_s * sr:
        return None
    e = 10 * np.log10(np.mean(pcm[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-10)
    speech_level = np.percentile(e, 90)
    thr = min(-38.0, speech_level - 18.0)
    lo = int(min_s * sr / hop)
    hi = min(n, int(max_s * sr / hop))
    quiet = e < thr
    best = None
    run = 0
    for i in range(lo, hi):
        run = run + 1 if quiet[i] else 0
        if run >= 15:  # >= 300 ms of pause
            best = i - run // 2
    if best is not None:
        return best * hop
    if n >= int(max_s * sr / hop):
        a = int(max_s * 0.6 * sr / hop)
        return (a + int(np.argmin(e[a:hi]))) * hop
    return None


class StreamingTranscriber:
    def __init__(self, engine: WhisperEngine, transcript: Transcript, *, language: str | None = None,
                 min_chunk_s: float = 5.0, max_chunk_s: float = 14.0, max_pending: int = 8,
                 on_update: Callable[[list[ASRSegment]], None] | None = None) -> None:
        self.engine = engine
        self.transcript = transcript
        self.language = language
        self._lang_locked = language is not None
        self.min_chunk_s = min_chunk_s
        self.max_chunk_s = max_chunk_s
        self.max_pending = max_pending
        self.on_update = on_update
        self.speakers = SpeakerTracker()
        self._acc = np.zeros(0, dtype=np.float32)
        self._acc_t0: float | None = None
        self._lock = threading.RLock()  # re-entrant: done-callbacks may run synchronously
        self._seq = 0
        self._next_apply = 0
        self._done: dict[int, tuple[float, float, np.ndarray, ASRResult | None]] = {}
        self.pending = 0
        self.dropped_seconds = 0.0
        self.fed_until = 0.0
        self.errors = 0

    # --- feeding ------------------------------------------------------------------
    def feed(self, pcm: np.ndarray, t_start: float) -> None:
        with self._lock:
            if self._acc_t0 is None:
                self._acc_t0 = t_start
            expected = self._acc_t0 + len(self._acc) / SR
            if t_start > expected + 0.05:  # timeline gap: pad with silence to stay aligned
                self._acc = np.concatenate([self._acc, np.zeros(int((t_start - expected) * SR), dtype=np.float32)])
            self._acc = np.concatenate([self._acc, pcm.astype(np.float32)])
            self.fed_until = t_start + len(pcm) / SR
            while True:
                cut = find_cut(self._acc, self.min_chunk_s, self.max_chunk_s)
                if cut is None:
                    break
                chunk, self._acc = self._acc[:cut], self._acc[cut:]
                t0 = self._acc_t0
                self._acc_t0 = t0 + cut / SR
                self._submit(chunk, t0)

    def flush(self) -> None:
        with self._lock:
            if self._acc_t0 is not None and len(self._acc) > SR * 0.5:
                chunk, t0 = self._acc, self._acc_t0
                self._acc = np.zeros(0, dtype=np.float32)
                self._acc_t0 = t0 + len(chunk) / SR
                self._submit(chunk, t0)

    def _submit(self, chunk: np.ndarray, t0: float) -> None:
        seq = self._seq
        self._seq += 1
        if self.pending >= self.max_pending:
            # ASR cannot keep up: degrade instead of blocking capture (reported in stats)
            self.dropped_seconds += len(chunk) / SR
            self._complete(seq, t0, chunk, None)
            return
        rms = float(np.sqrt(np.mean(chunk**2) + 1e-12))
        if 20 * np.log10(rms) < -55:  # pure silence: skip the model call
            self._complete(seq, t0, chunk, ASRResult([], self.language))
            return
        self.pending += 1
        prompt = self.transcript.tail_text()
        fut = self.engine.submit(chunk, self.language, prompt)
        fut.add_done_callback(lambda f, s=seq, t=t0, c=chunk: self._on_done(f, s, t, c))

    def _on_done(self, fut: Future, seq: int, t0: float, chunk: np.ndarray) -> None:
        with self._lock:
            self.pending -= 1
        try:
            res = fut.result()
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            log.warning("ASR chunk failed: %s", exc)
            res = None
        self._complete(seq, t0, chunk, res)

    def _complete(self, seq: int, t0: float, chunk: np.ndarray, res: ASRResult | None) -> None:
        with self._lock:
            self._done[seq] = (t0, len(chunk) / SR, chunk, res)
            while self._next_apply in self._done:
                ct0, dur, cpcm, cres = self._done.pop(self._next_apply)
                self._next_apply += 1
                self._apply(ct0, dur, cpcm, cres)

    def _apply(self, t0: float, dur: float, pcm: np.ndarray, res: ASRResult | None) -> None:
        segs: list[ASRSegment] = []
        if res is not None:
            if not self._lang_locked and res.language and res.language_prob > 0.7:
                self.language = res.language
                self._lang_locked = True
                self.transcript.language = res.language
            for s in dedupe_loops(res.segments):
                a, b = int(max(0, s.start) * SR), int(max(0, s.end) * SR)
                seg_pcm = pcm[a:b]
                rms_db = float(20 * np.log10(np.sqrt(np.mean(seg_pcm**2) + 1e-12))) if len(seg_pcm) else -90.0
                if is_hallucination(s, rms_db):
                    continue
                spk, _conf = self.speakers.assign(seg_pcm)
                words = [Word(w.w, t0 + w.s, t0 + w.e, w.p, spk) for w in s.words]
                segs.append(ASRSegment(t0 + s.start, t0 + s.end, s.text, words, s.avg_logprob, s.no_speech_prob, spk))
            tidy_words(segs)
        self.transcript.add(segs, t0 + dur)
        if self.on_update and segs:
            try:
                self.on_update(segs)
            except Exception:  # pragma: no cover
                log.exception("transcript update callback failed")

    @property
    def lag_seconds(self) -> float:
        return max(0.0, self.fed_until - self.transcript.transcribed_until)

    def wait_idle(self, timeout: float = 60.0) -> bool:
        t = time.time()
        while time.time() - t < timeout:
            if self.pending == 0:
                with self._lock:
                    if not self._done:
                        return True
            time.sleep(0.05)
        return False
