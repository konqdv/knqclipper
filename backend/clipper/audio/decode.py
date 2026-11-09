"""Decode audio / video frames from buffer segments with PyAV (in-process, no subprocess per segment)."""

from __future__ import annotations

import logging
from pathlib import Path

import av
import numpy as np

log = logging.getLogger(__name__)
SR = 16000


def decode_audio(path: Path | str, sr: int = SR, expected_duration: float | None = None) -> np.ndarray:
    """Return mono float32 PCM in [-1, 1]. Pads/trims to ``expected_duration`` to keep the timeline exact."""
    chunks: list[np.ndarray] = []
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                n = int(round((expected_duration or 0) * sr))
                return np.zeros(n, dtype=np.float32)
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="flt", layout="mono", rate=sr)
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray().reshape(-1))
    except (av.error.InvalidDataError, av.error.FFmpegError) as exc:  # corrupted segment: keep timeline with silence
        log.warning("audio decode failed for %s: %s", path, exc)
    pcm = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, dtype=np.float32)
    if expected_duration is not None:
        n = int(round(expected_duration * sr))
        if len(pcm) < n:
            pcm = np.pad(pcm, (0, n - len(pcm)))
        else:
            pcm = pcm[:n]
    return pcm


def decode_frames(path: Path | str, fps: float = 2.0, width: int = 320, max_frames: int | None = None,
                  start: float = 0.0, end: float | None = None) -> list[tuple[float, np.ndarray]]:
    """Sample BGR frames at ~``fps`` (timestamps relative to file start), resized to ``width``."""
    out: list[tuple[float, np.ndarray]] = []
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                return out
            vs = container.streams.video[0]
            vs.thread_type = "AUTO"
            t0 = None
            next_t = start
            if start > 0:
                container.seek(int(start / vs.time_base), stream=vs, any_frame=False, backward=True)
            for frame in container.decode(vs):
                if frame.pts is None:
                    continue
                t = float(frame.pts * vs.time_base)
                if t0 is None:
                    t0 = float(vs.start_time * vs.time_base) if vs.start_time is not None else t
                rel = t - t0
                if end is not None and rel > end:
                    break
                if rel + 1e-6 < next_t:
                    continue
                h = int(round(frame.height * width / frame.width / 2) * 2)
                img = frame.to_ndarray(format="bgr24", width=width, height=h)
                out.append((rel, img))
                next_t = rel + 1.0 / fps
                if max_frames and len(out) >= max_frames:
                    break
    except (av.error.InvalidDataError, av.error.FFmpegError) as exc:
        log.warning("video decode failed for %s: %s", path, exc)
    return out


def probe_video(path: Path | str) -> dict:
    with av.open(str(path)) as c:
        info = {"duration": float(c.duration / 1e6) if c.duration else 0.0, "width": None, "height": None, "fps": None,
                "has_audio": bool(c.streams.audio), "has_video": bool(c.streams.video)}
        if c.streams.video:
            v = c.streams.video[0]
            info.update(width=v.codec_context.width, height=v.codec_context.height,
                        fps=float(v.average_rate) if v.average_rate else None)
        return info
