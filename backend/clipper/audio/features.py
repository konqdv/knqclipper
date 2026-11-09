"""Frame-level acoustic features and a bounded time-indexed feature track.

Two resolutions:
* 100 ms frames: loudness, zero-crossing rate, spectral centroid/flatness, F0 (autocorrelation), voicing.
* 10 ms envelope: used for syllabic-rate modulation (laughter) and onset detection.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

SR = 16000
HOP = 1600  # 100 ms
ENV_HOP = 160  # 10 ms
NFFT = 2048
_EPS = 1e-10


@dataclass
class FrameFeatures:
    t: np.ndarray  # frame start (stream time)
    rms_db: np.ndarray
    zcr: np.ndarray
    centroid: np.ndarray  # Hz
    flatness: np.ndarray
    f0: np.ndarray  # Hz, 0 when unvoiced
    voiced: np.ndarray  # bool
    env_t0: float = 0.0
    env: np.ndarray | None = None  # 10 ms RMS envelope (linear)


def _frames(x: np.ndarray, hop: int) -> np.ndarray:
    n = len(x) // hop
    return x[: n * hop].reshape(n, hop) if n else np.zeros((0, hop), dtype=x.dtype)


def compute_features(pcm: np.ndarray, t0: float, sr: int = SR) -> FrameFeatures:
    fr = _frames(pcm.astype(np.float32), HOP)
    n = len(fr)
    if n == 0:
        z = np.zeros(0)
        return FrameFeatures(z, z, z, z, z, z, z.astype(bool), t0, np.zeros(0))
    rms = np.sqrt(np.mean(fr**2, axis=1) + _EPS)
    rms_db = 20 * np.log10(rms + _EPS)
    zcr = np.mean(np.abs(np.diff(np.signbit(fr).astype(np.int8), axis=1)), axis=1)
    win = np.hanning(HOP).astype(np.float32)
    spec = np.abs(np.fft.rfft(fr * win, n=NFFT, axis=1)) + _EPS
    freqs = np.fft.rfftfreq(NFFT, 1 / sr)
    centroid = (spec * freqs).sum(1) / spec.sum(1)
    flatness = np.exp(np.mean(np.log(spec), axis=1)) / np.mean(spec, axis=1)
    # F0 by autocorrelation (via power spectrum) on the 100 ms frame
    power = spec**2
    acf = np.fft.irfft(power, axis=1)[:, : sr // 60]
    lo, hi = sr // 500, sr // 70  # 70-500 Hz
    seg = acf[:, lo:hi]
    lag = np.argmax(seg, axis=1) + lo
    peak = seg[np.arange(n), lag - lo] / (acf[:, 0] + _EPS)
    voiced = (peak > 0.35) & (rms_db > -45)
    f0 = np.where(voiced, sr / lag, 0.0)
    env = np.sqrt(np.mean(_frames(pcm.astype(np.float32), ENV_HOP) ** 2, axis=1) + _EPS)
    t = t0 + np.arange(n) * (HOP / sr)
    return FrameFeatures(t, rms_db, zcr, centroid, flatness, f0, voiced, t0, env)


# --- MFCC (used for lightweight speaker-turn clustering) ---------------------------------
_MEL_CACHE: dict[tuple[int, int, int], np.ndarray] = {}


def _mel_filterbank(n_mels: int = 26, nfft: int = 512, sr: int = SR) -> np.ndarray:
    key = (n_mels, nfft, sr)
    if key in _MEL_CACHE:
        return _MEL_CACHE[key]

    def hz2mel(f):
        return 2595 * np.log10(1 + f / 700)

    def mel2hz(m):
        return 700 * (10 ** (m / 2595) - 1)

    mels = np.linspace(hz2mel(60), hz2mel(sr / 2 - 200), n_mels + 2)
    bins = np.floor((nfft + 1) * mel2hz(mels) / sr).astype(int)
    fb = np.zeros((n_mels, nfft // 2 + 1))
    for i in range(1, n_mels + 1):
        a, b, c = bins[i - 1], bins[i], bins[i + 1]
        if b > a:
            fb[i - 1, a:b] = (np.arange(a, b) - a) / (b - a)
        if c > b:
            fb[i - 1, b:c] = (c - np.arange(b, c)) / (c - b)
    _MEL_CACHE[key] = fb
    return fb


def mfcc(pcm: np.ndarray, sr: int = SR, n_mfcc: int = 13) -> np.ndarray:
    """MFCC matrix (frames x n_mfcc) with 25 ms windows / 10 ms hop."""
    win, hop, nfft = int(0.025 * sr), int(0.010 * sr), 512
    if len(pcm) < win:
        return np.zeros((0, n_mfcc))
    n = 1 + (len(pcm) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = pcm[idx] * np.hamming(win)
    pspec = np.abs(np.fft.rfft(frames, nfft)) ** 2 / nfft
    mel = np.log(pspec @ _mel_filterbank(26, nfft, sr).T + _EPS)
    k = np.arange(26)
    dct = np.cos(np.pi / 26 * (k[None, :] + 0.5) * np.arange(n_mfcc)[:, None])
    return mel @ dct.T


class FeatureTrack:
    """Thread-safe append-only feature store with a bounded horizon (default 30 min)."""

    def __init__(self, horizon_s: float = 1800.0) -> None:
        self.horizon_s = horizon_s
        self._lock = threading.Lock()
        self._chunks: list[FrameFeatures] = []

    def add(self, f: FrameFeatures) -> None:
        if len(f.t) == 0:
            return
        with self._lock:
            self._chunks.append(f)
            newest = f.t[-1]
            while self._chunks and self._chunks[0].t[-1] < newest - self.horizon_s:
                self._chunks.pop(0)

    def window(self, t0: float, t1: float) -> FrameFeatures:
        with self._lock:
            chunks = [c for c in self._chunks if len(c.t) and c.t[-1] >= t0 and c.t[0] <= t1]
        if not chunks:
            z = np.zeros(0)
            return FrameFeatures(z, z, z, z, z, z, z.astype(bool), t0, np.zeros(0))
        cat = {k: np.concatenate([getattr(c, k) for c in chunks]) for k in ("t", "rms_db", "zcr", "centroid", "flatness", "f0", "voiced")}
        m = (cat["t"] >= t0) & (cat["t"] < t1)
        # envelope: concatenate and slice by time
        env_parts, env_t0 = [], None
        for c in chunks:
            if c.env is None or len(c.env) == 0:
                continue
            if env_t0 is None:
                env_t0 = c.env_t0
            env_parts.append(c.env)
        env = np.concatenate(env_parts) if env_parts else np.zeros(0)
        if env_t0 is not None and len(env):
            a = max(0, int((t0 - env_t0) / 0.01))
            b = max(a, int((t1 - env_t0) / 0.01))
            env = env[a:b]
            env_t0 = env_t0 + a * 0.01
        return FrameFeatures(**{k: v[m] for k, v in cat.items()}, env_t0=env_t0 or t0, env=env)

    @property
    def end_time(self) -> float:
        with self._lock:
            return float(self._chunks[-1].t[-1] + 0.1) if self._chunks else 0.0
