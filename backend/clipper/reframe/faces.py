"""Face detection: YuNet (OpenCV DNN, MIT) with Haar-cascade fallback (bundled with OpenCV)."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Face:
    x: float  # normalized [0,1] box
    y: float
    w: float
    h: float
    score: float
    landmarks: np.ndarray | None = None  # 5x2 normalized (r-eye, l-eye, nose, r-mouth, l-mouth)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h

    def mouth_ratio(self) -> float | None:
        """Mouth width / inter-ocular distance: grows with smiles/laughs (cheap expression cue)."""
        if self.landmarks is None:
            return None
        re_, le, _, rm, lm = self.landmarks
        eye = np.linalg.norm(le - re_)
        return float(np.linalg.norm(lm - rm) / eye) if eye > 1e-6 else None


_model_path: str | None = None
_model_lock = threading.Lock()


def _yunet_model(models_dir: Path | None) -> str | None:
    global _model_path
    with _model_lock:
        if _model_path is not None:
            return _model_path or None
        try:
            from huggingface_hub import hf_hub_download

            kwargs = {"cache_dir": str(models_dir / "hf" / "hub")} if models_dir else {}
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            _model_path = hf_hub_download("opencv/face_detection_yunet", "face_detection_yunet_2023mar.onnx", **kwargs)
        except Exception as exc:  # offline: fall back to Haar
            log.warning("YuNet unavailable (%s); using Haar cascade", exc)
            _model_path = ""
        return _model_path or None


class FaceDetector:
    """Not thread-safe per instance; create one per worker thread."""

    def __init__(self, models_dir: Path | None = None, score_threshold: float = 0.7) -> None:
        self.backend = "haar"
        self._yunet = None
        path = _yunet_model(models_dir)
        if path and hasattr(cv2, "FaceDetectorYN"):
            try:
                self._yunet = cv2.FaceDetectorYN.create(path, "", (320, 320), score_threshold, 0.3, 50)
                self.backend = "yunet"
            except cv2.error as exc:
                log.warning("YuNet init failed: %s", exc)
        if self._yunet is None:
            self._haar = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))

    def detect(self, img: np.ndarray) -> list[Face]:
        h, w = img.shape[:2]
        if self._yunet is not None:
            self._yunet.setInputSize((w, h))
            _, faces = self._yunet.detect(img)
            out = []
            for f in faces if faces is not None else []:
                x, y, fw, fh = f[:4]
                lm = f[4:14].reshape(5, 2) / np.array([w, h])
                out.append(Face(max(0.0, x / w), max(0.0, y / h), fw / w, fh / h, float(f[14]), lm))
            return out
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        rects = self._haar.detectMultiScale(gray, 1.1, 5, minSize=(max(16, w // 30), max(16, w // 30)))
        return [Face(x / w, y / h, fw / w, fh / h, 0.8) for (x, y, fw, fh) in rects]
