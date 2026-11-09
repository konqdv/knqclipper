"""Lightweight online speaker-turn labelling.

Each ASR segment gets an MFCC statistics embedding (mean + std); embeddings are clustered online
with cosine similarity. It is an approximation (no neural diarization) that is good enough to tell
"a different person answered" in podcasts/interviews, and it reports its own confidence.
"""

from __future__ import annotations

import numpy as np

from clipper.audio.features import mfcc


class SpeakerTracker:
    def __init__(self, threshold: float = 0.80, max_speakers: int = 6) -> None:
        self.threshold = threshold
        self.max_speakers = max_speakers
        self.centroids: list[np.ndarray] = []
        self.counts: list[int] = []

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

    def assign(self, pcm: np.ndarray) -> tuple[str | None, float]:
        emb = self.embedding(pcm)
        if emb is None:
            return None, 0.0
        if not self.centroids:
            self.centroids.append(emb)
            self.counts.append(1)
            return "S1", 0.5
        sims = [float(c @ emb / (np.linalg.norm(c) + 1e-9)) for c in self.centroids]
        best = int(np.argmax(sims))
        if sims[best] >= self.threshold or len(self.centroids) >= self.max_speakers:
            k = self.counts[best]
            self.centroids[best] = (self.centroids[best] * k + emb) / (k + 1)
            self.counts[best] = k + 1
            return f"S{best + 1}", min(1.0, sims[best])
        self.centroids.append(emb)
        self.counts.append(1)
        return f"S{len(self.centroids)}", 1.0 - max(sims)
