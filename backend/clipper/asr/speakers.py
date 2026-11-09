"""Lightweight online speaker-turn labelling.

Each ASR segment gets a voice signature: MFCC statistics (timbre) + median pitch in semitones.
Signatures are clustered online. MFCC similarity alone barely separates voices (same speaker
~0.97-0.99 cosine vs different speakers ~0.91-0.96 in our tests), so a pitch-distance penalty is
combined with it. It is an approximation (no neural diarization) that is good enough to tell
"a different person answered" in podcasts/interviews, and it reports its own confidence.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from clipper.audio.features import compute_features, mfcc


@dataclass
class Voice:
    timbre: np.ndarray
    pitch: float | None  # median pitch, semitones re 100 Hz
    n: int = 1


class SpeakerTracker:
    def __init__(self, threshold: float = 0.93, pitch_tolerance: float = 1.5, max_speakers: int = 6) -> None:
        self.threshold = threshold
        self.pitch_tolerance = pitch_tolerance
        self.max_speakers = max_speakers
        self.voices: list[Voice] = []

    @staticmethod
    def embedding(pcm: np.ndarray) -> np.ndarray | None:
        m = mfcc(pcm)
        if len(m) < 30:  # < 0.3 s of speech
            return None
        m = m[:, 1:]  # drop energy coefficient
        emb = np.concatenate([m.mean(0), m.std(0)])
        emb = emb - emb.mean()
        n = np.linalg.norm(emb)
        return emb / n if n > 0 else None

    @staticmethod
    def pitch(pcm: np.ndarray) -> float | None:
        f = compute_features(pcm, 0.0)
        voiced = f.f0[f.voiced]
        if len(voiced) < 5:
            return None
        return float(12 * np.log2(np.median(voiced) / 100.0))

    def similarity(self, v: Voice, emb: np.ndarray, pitch: float | None) -> float:
        cos = float(v.timbre @ emb / (np.linalg.norm(v.timbre) + 1e-9))
        if pitch is not None and v.pitch is not None:
            cos -= 0.04 * max(0.0, abs(pitch - v.pitch) - self.pitch_tolerance)
        return cos

    def assign(self, pcm: np.ndarray) -> tuple[str | None, float]:
        emb = self.embedding(pcm)
        if emb is None:
            return None, 0.0
        p = self.pitch(pcm)
        if not self.voices:
            self.voices.append(Voice(emb, p))
            return "S1", 0.5
        sims = [self.similarity(v, emb, p) for v in self.voices]
        best = int(np.argmax(sims))
        if sims[best] >= self.threshold or len(self.voices) >= self.max_speakers:
            v = self.voices[best]
            v.timbre = (v.timbre * v.n + emb) / (v.n + 1)
            if p is not None:
                v.pitch = p if v.pitch is None else (v.pitch * v.n + p) / (v.n + 1)
            v.n += 1
            return f"S{best + 1}", float(min(1.0, max(0.0, sims[best])))
        self.voices.append(Voice(emb, p))
        return f"S{len(self.voices)}", float(min(1.0, max(0.0, 1.0 - max(sims))))
