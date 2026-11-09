"""Emotion Detector: acoustic arousal and vocal events.

Signals are always relative to the stream's own baseline (a loud streamer is not "always shouting").
A reaction is never treated as a highlight by itself: every event carries the time window *before*
it (``cause_window``) so the highlight engine can examine what provoked it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import numpy as np

from clipper.audio.features import FeatureTrack, FrameFeatures


@dataclass
class VocalEvent:
    type: str  # laughter | shout | surprise | anger | dramatic_pause | tone_shift | intense_talk
    t: float
    duration: float
    strength: float  # 0..1
    cause_window: tuple[float, float] = (0.0, 0.0)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EmotionSummary:
    arousal: float  # 0..1 peak arousal in the window
    mean_arousal: float
    loudness_z: float
    pitch_var_z: float
    voiced_ratio: float
    events: list[VocalEvent] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["events"] = [e.to_dict() for e in self.events]
        return d


def _robust_z(x: np.ndarray, ref: np.ndarray) -> np.ndarray:
    if len(ref) < 10:
        return np.zeros_like(x)
    med = np.median(ref)
    mad = np.median(np.abs(ref - med)) * 1.4826 + 1e-6
    return (x - med) / mad


def _semitones(f0: np.ndarray) -> np.ndarray:
    return 12 * np.log2(np.maximum(f0, 1.0) / 100.0)


def _sigmoid(x: float) -> float:
    return float(1 / (1 + np.exp(-x)))


class EmotionDetector:
    def __init__(self, track: FeatureTrack, baseline_s: float = 180.0) -> None:
        self.track = track
        self.baseline_s = baseline_s

    def _baseline(self, t0: float) -> FrameFeatures:
        return self.track.window(max(0.0, t0 - self.baseline_s), t0)

    def arousal_series(self, t0: float, t1: float) -> tuple[np.ndarray, np.ndarray]:
        """Per-frame arousal 0..1 combining loudness z and pitch z (relative to baseline)."""
        w = self.track.window(t0, t1)
        if len(w.t) == 0:
            return np.zeros(0), np.zeros(0)
        base = self._baseline(t0)
        ref_db = base.rms_db[base.rms_db > -50] if len(base.t) else w.rms_db
        lz = _robust_z(w.rms_db, ref_db if len(ref_db) >= 10 else w.rms_db)
        st = _semitones(w.f0)
        bst = _semitones(base.f0[base.voiced]) if len(base.t) and base.voiced.any() else st[w.voiced]
        pz = np.where(w.voiced, _robust_z(st, bst if len(bst) >= 10 else st), 0.0)
        raw = 0.65 * np.clip(lz, -3, 6) + 0.35 * np.clip(pz, -3, 6)
        ar = 1 / (1 + np.exp(-(raw - 1.5)))
        return w.t, ar

    def laughter_score(self, w: FrameFeatures, baseline_f0: float | None = None) -> float:
        """Laughter cue: a *regular* 3.5–7 Hz burst rhythm (ha-ha-ha), strong voiced/breathy alternation and
        raised pitch. Ordinary speech also has a ~4–5 Hz syllable rate, but its rhythm is irregular, so
        rhythm regularity (spectral peakiness) is required, not just modulation energy."""
        env = w.env
        if env is None or len(env) < 100 or len(w.voiced) < 8:
            return 0.0
        e = env - env.mean()
        spec = np.abs(np.fft.rfft(e * np.hanning(len(e)))) ** 2
        freqs = np.fft.rfftfreq(len(e), 0.01)
        band = spec[(freqs >= 3.5) & (freqs <= 7.0)]
        ref = spec[(freqs >= 0.5) & (freqs <= 15)]
        if len(band) == 0 or ref.sum() <= 0:
            return 0.0
        mod_ratio = band.sum() / ref.sum()
        peakiness = band.max() / (np.median(ref) + 1e-12)  # regular rhythm -> one dominant modulation peak
        alternation = float(np.mean(np.abs(np.diff(w.voiced.astype(int)))))
        loud = w.rms_db > -40
        breathy = float(np.mean(w.flatness[loud])) if loud.any() else 0.0
        f0 = float(np.median(w.f0[w.voiced])) if w.voiced.any() else 0.0
        pitch_up = (f0 / baseline_f0) if (baseline_f0 and f0) else 1.0
        if peakiness < 25 or mod_ratio < 0.35 or alternation < 0.25:
            return 0.0
        score = (0.35 * min(1.0, (peakiness - 25) / 60) + 0.25 * min(1.0, mod_ratio / 0.6)
                 + 0.2 * min(1.0, alternation / 0.5) + 0.1 * min(1.0, breathy / 0.2)
                 + 0.1 * float(np.clip((pitch_up - 1.0) / 0.3, 0, 1)))
        return float(np.clip(score, 0, 1))

    def summarize(self, t0: float, t1: float) -> EmotionSummary:
        t, ar = self.arousal_series(t0, t1)
        w = self.track.window(t0, t1)
        if len(t) == 0:
            return EmotionSummary(0.0, 0.0, 0.0, 0.0, 0.0)
        base = self._baseline(t0)
        ref_db = base.rms_db[base.rms_db > -50] if len(base.t) else w.rms_db
        lz = _robust_z(w.rms_db, ref_db if len(ref_db) >= 10 else w.rms_db)
        voiced_ratio = float(np.mean(w.voiced)) if len(w.voiced) else 0.0
        st = _semitones(w.f0[w.voiced])
        bst = _semitones(base.f0[base.voiced]) if len(base.t) and base.voiced.any() else st
        pvz = 0.0
        if len(st) > 10 and len(bst) > 10:
            pvz = float((np.std(st) - np.std(bst)) / (np.std(bst) + 1e-6))
        events = self.detect_events(t0, t1, t, ar, w, lz)
        return EmotionSummary(float(ar.max()), float(ar.mean()), float(np.percentile(lz, 95)), pvz, voiced_ratio, events)

    def detect_events(self, t0: float, t1: float, t: np.ndarray, ar: np.ndarray, w: FrameFeatures,
                      lz: np.ndarray) -> list[VocalEvent]:
        events: list[VocalEvent] = []
        n = len(t)
        if n == 0:
            return events
        # laughter in sliding 1.5 s windows
        step, size = 5, 15
        base = self._baseline(t0)
        bf0 = float(np.median(base.f0[base.voiced])) if len(base.t) and base.voiced.any() else None
        for i in range(0, max(1, n - size + 1), step):
            a, b = t[i], t[min(n - 1, i + size - 1)] + 0.1
            sub = self.track.window(a, b)
            ls = self.laughter_score(sub, bf0)
            if ls > 0.45:
                if events and events[-1].type == "laughter" and a - (events[-1].t + events[-1].duration) < 0.6:
                    ev = events[-1]
                    ev.duration = b - ev.t
                    ev.strength = max(ev.strength, ls)
                else:
                    events.append(VocalEvent("laughter", a, b - a, ls, (max(0.0, a - 15), a)))
        # shouts: sustained high loudness + high pitch
        loud = (lz > 3.0) & w.voiced
        events += self._runs(t, loud, "shout", min_frames=3, strength=lambda i0, i1: float(np.clip(lz[i0:i1].mean() / 6, 0, 1)))
        # surprise: sudden onset (>= 12 dB jump within 300 ms after a calmer moment)
        db = w.rms_db
        for i in range(3, n):
            jump = db[i] - db[max(0, i - 3): i].min()
            if jump >= 12 and db[i] > -30 and (not events or abs(events[-1].t - t[i]) > 1.0):
                events.append(VocalEvent("surprise", float(t[i]), 0.5, float(np.clip(jump / 24, 0, 1)), (max(0.0, t[i] - 12), float(t[i]))))
        # anger proxy: sustained loud + bright spectrum but not high pitch-laughter pattern
        angry = (lz > 2.0) & (w.centroid > np.percentile(w.centroid, 75)) & w.voiced
        events += self._runs(t, angry, "anger", min_frames=10, strength=lambda i0, i1: float(np.clip(lz[i0:i1].mean() / 5, 0, 1)))
        # dramatic pause: >= 1.5 s silence between speech with speech resuming
        silent = db < max(-45.0, float(np.percentile(db, 10)) + 3)
        for ev in self._runs(t, silent, "dramatic_pause", min_frames=15, strength=lambda i0, i1: float(np.clip((i1 - i0) / 40, 0, 1))):
            if ev.t > t[0] + 2 and ev.t + ev.duration < t[-1] - 1:
                events.append(ev)
        # intense talk: sustained high arousal for >= 4 s (heated discussion / excitement)
        events += self._runs(t, ar > 0.7, "intense_talk", min_frames=40, strength=lambda i0, i1: float(ar[i0:i1].mean()))
        # tone shift: change in mean pitch/energy between consecutive 4 s windows
        if n >= 80:
            for i in range(40, n - 40, 20):
                a, b = ar[i - 40: i].mean(), ar[i: i + 40].mean()
                if b - a > 0.35:
                    events.append(VocalEvent("tone_shift", float(t[i]), 4.0, float(np.clip((b - a) * 2, 0, 1)), (max(0.0, t[i] - 15), float(t[i]))))
        events.sort(key=lambda e: e.t)
        return events

    @staticmethod
    def _runs(t: np.ndarray, mask: np.ndarray, kind: str, min_frames: int, strength) -> list[VocalEvent]:
        out = []
        i, n = 0, len(mask)
        while i < n:
            if mask[i]:
                j = i
                while j < n and mask[j]:
                    j += 1
                if j - i >= min_frames:
                    out.append(VocalEvent(kind, float(t[i]), float(t[j - 1] - t[i] + 0.1), strength(i, j),
                                          (max(0.0, float(t[i]) - 15), float(t[i]))))
                i = j
            else:
                i += 1
        return out

    def quick_trigger(self, t0: float, t1: float) -> tuple[float, list[str]]:
        """Fast 0..1 trigger value for the most recent window (used every engine tick)."""
        t, ar = self.arousal_series(t0, t1)
        if len(ar) == 0:
            return 0.0, []
        w = self.track.window(t0, t1)
        reasons = []
        val = float(np.percentile(ar, 90))
        if val > 0.6:
            reasons.append("pico de energía vocal")
        ls = self.laughter_score(w)
        if ls > 0.5:
            reasons.append("risas")
            val = max(val, 0.5 + ls / 2)
        return float(np.clip(val, 0, 1)), reasons


def sigmoid(x: float) -> float:
    return _sigmoid(x)


_LAUGH_TOKEN = re.compile(r"^(?:[jh][aeiou]){1,}[jh]?[aeiou]?$|^(?:lol|lmao|xd)+$", re.I)


def filter_laughter_by_words(summary: EmotionSummary, words, max_wps: float = 1.2) -> EmotionSummary:
    """Drop "laughter" events that overlap intelligible speech: Whisper transcribes words, not laughs.
    Tokens like "jaja"/"haha" are kept as laughter evidence."""
    kept = []
    for e in summary.events:
        if e.type == "laughter":
            inside = [w for w in words if w.e > e.t and w.s < e.t + e.duration]
            speech = [w for w in inside if not _LAUGH_TOKEN.match(w.w.strip(".,!?¡¿ ").lower())]
            if len(speech) / max(0.5, e.duration) > max_wps:
                continue
        kept.append(e)
    summary.events = kept
    return summary
