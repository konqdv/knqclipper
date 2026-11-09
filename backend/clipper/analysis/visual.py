"""Visual Analyzer: content-agnostic visual signals per sampled frame.

Does not assume faces, webcams or games exist: it measures scene changes, motion and saliency for
any content and adds face/people/expression cues only when faces are actually present.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from clipper.reframe.faces import Face, FaceDetector


@dataclass
class VisualFrame:
    t: float
    scene_cut: bool
    hist_diff: float
    motion: float  # mean abs diff, 0..1
    brightness: float
    n_faces: int
    face_area: float  # largest face area fraction
    face_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    mouth_ratio: float | None = None
    edge_density: float = 0.0  # high for slides/text/HUD-heavy frames


@dataclass
class VisualSummary:
    frames: int
    scene_cuts: int
    motion_mean: float
    motion_peak_z: float
    faces_mean: float
    max_faces: int
    face_area_mean: float
    people_change: bool
    expression_change: float
    edge_density: float
    events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class VisualAnalyzer:
    def __init__(self, detector: FaceDetector | None = None, horizon_s: float = 1800.0) -> None:
        self.detector = detector
        self.horizon_s = horizon_s
        self._frames: list[VisualFrame] = []
        self._lock = threading.Lock()
        self._prev_hist: np.ndarray | None = None
        self._prev_gray: np.ndarray | None = None

    def process(self, frames: list[tuple[float, np.ndarray]], t_offset: float, detect_faces: bool = True) -> list[VisualFrame]:
        out: list[VisualFrame] = []
        for rel_t, img in frames:
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (160, max(2, int(160 * gray.shape[0] / gray.shape[1]))))
            hd = float(cv2.compareHist(self._prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA)) if self._prev_hist is not None else 0.0
            motion = float(np.mean(cv2.absdiff(small, self._prev_gray)) / 255.0) if self._prev_gray is not None and self._prev_gray.shape == small.shape else 0.0
            self._prev_hist, self._prev_gray = hist, small
            faces: list[Face] = self.detector.detect(img) if (detect_faces and self.detector) else []
            largest = max(faces, key=lambda f: f.area) if faces else None
            edges = cv2.Canny(small, 80, 160)
            out.append(VisualFrame(
                t=t_offset + rel_t, scene_cut=hd > 0.55, hist_diff=hd, motion=motion,
                brightness=float(gray.mean() / 255.0), n_faces=len(faces), face_area=largest.area if largest else 0.0,
                face_boxes=[(f.x, f.y, f.w, f.h) for f in faces[:6]], mouth_ratio=largest.mouth_ratio() if largest else None,
                edge_density=float(np.mean(edges > 0)),
            ))
        with self._lock:
            self._frames.extend(out)
            if self._frames:
                newest = self._frames[-1].t
                while self._frames and self._frames[0].t < newest - self.horizon_s:
                    self._frames.pop(0)
        return out

    def window(self, t0: float, t1: float) -> list[VisualFrame]:
        with self._lock:
            return [f for f in self._frames if t0 <= f.t < t1]

    def summarize(self, t0: float, t1: float, baseline_s: float = 180.0) -> VisualSummary:
        fr = self.window(t0, t1)
        if not fr:
            return VisualSummary(0, 0, 0.0, 0.0, 0.0, 0, 0.0, False, 0.0, 0.0)
        base = self.window(max(0.0, t0 - baseline_s), t0)
        mot = np.array([f.motion for f in fr])
        bm = np.array([f.motion for f in base]) if len(base) > 10 else mot
        med, mad = float(np.median(bm)), float(np.median(np.abs(bm - np.median(bm))) * 1.4826 + 1e-3)
        peak_z = float((mot.max() - med) / mad)
        nf = [f.n_faces for f in fr]
        mr = [f.mouth_ratio for f in fr if f.mouth_ratio is not None]
        expr = float(np.ptp(mr)) if len(mr) >= 3 else 0.0
        events = []
        for f in fr:
            if f.scene_cut:
                events.append({"type": "scene_cut", "t": round(f.t, 2)})
        for i in range(1, len(fr)):
            if fr[i].n_faces != fr[i - 1].n_faces and max(fr[i].n_faces, fr[i - 1].n_faces) >= 1:
                events.append({"type": "people_change", "t": round(fr[i].t, 2), "from": fr[i - 1].n_faces, "to": fr[i].n_faces})
        if peak_z > 3:
            i = int(np.argmax(mot))
            events.append({"type": "motion_spike", "t": round(fr[i].t, 2), "z": round(peak_z, 1)})
        return VisualSummary(
            frames=len(fr), scene_cuts=sum(f.scene_cut for f in fr), motion_mean=float(mot.mean()), motion_peak_z=peak_z,
            faces_mean=float(np.mean(nf)), max_faces=int(max(nf)), face_area_mean=float(np.mean([f.face_area for f in fr])),
            people_change=any(e["type"] == "people_change" for e in events), expression_change=expr,
            edge_density=float(np.mean([f.edge_density for f in fr])), events=events[:20],
        )

    def quick_trigger(self, t0: float, t1: float) -> tuple[float, list[str]]:
        s = self.summarize(t0, t1)
        if s.frames == 0:
            return 0.0, []
        val, reasons = 0.0, []
        if s.motion_peak_z > 4:
            val = max(val, float(1 / (1 + np.exp(-(s.motion_peak_z - 6) / 2))))
            reasons.append("movimiento repentino")
        if s.expression_change > 0.25:
            val = max(val, 0.5)
            reasons.append("cambio de expresión facial")
        if s.people_change:
            val = max(val, 0.35)
            reasons.append("entra/sale una persona")
        return min(1.0, val), reasons
