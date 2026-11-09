"""Smart Reframing: plan how a horizontal source becomes a 9:16 frame.

Layouts:
* ``track``  – crop window follows the main subject / active speaker (podcasts, IRL, single cam).
* ``split``  – two stacked regions: webcam + gameplay, or two speakers.
* ``fit``    – whole frame over a blurred fill (slides, wide scenes, anything a crop would ruin).
* ``center`` – static centre crop.
* ``manual`` – user-chosen centre/zoom (from the editor).

``auto`` picks a layout from what is actually on screen; it never assumes faces exist and falls
back to saliency/motion tracking, or to ``fit`` when the important content is spread across the frame.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from clipper.audio.decode import decode_frames, probe_video
from clipper.reframe.faces import Face, FaceDetector

log = logging.getLogger(__name__)
OUT_W, OUT_H = 1080, 1920


@dataclass
class Region:
    x: float  # normalized source coordinates
    y: float
    w: float
    h: float


@dataclass
class ReframePlan:
    layout: str
    src_w: int
    src_h: int
    crop_w: int = 0
    crop_h: int = 0
    keyframes: list[tuple[float, float]] = field(default_factory=list)  # (t, crop x in px), clip-relative
    y: int = 0
    regions: list[dict[str, float]] = field(default_factory=list)  # for split: [{x,y,w,h,out_h}]
    reason: str = ""
    alternatives: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReframePlan":
        d = dict(d)
        d["keyframes"] = [tuple(k) for k in d.get("keyframes", [])]
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# --- tracking --------------------------------------------------------------------------
@dataclass
class Track:
    id: int
    boxes: dict[int, Face] = field(default_factory=dict)  # frame index -> face
    activity: dict[int, float] = field(default_factory=dict)

    def last(self) -> Face:
        return self.boxes[max(self.boxes)]


def _associate(tracks: list[Track], faces: list[Face], fi: int, max_dist: float = 0.12) -> None:
    used = set()
    for f in sorted(faces, key=lambda f: -f.area):
        best, bd = None, max_dist
        for tr in tracks:
            if tr.id in used or fi - max(tr.boxes) > 15:
                continue
            last = tr.last()
            d = np.hypot(last.cx - f.cx, last.cy - f.cy)
            if d < bd:
                best, bd = tr, d
        if best is None:
            best = Track(len(tracks))
            tracks.append(best)
        best.boxes[fi] = f
        used.add(best.id)


def _mouth_motion(prev: np.ndarray, cur: np.ndarray, f: Face) -> float:
    h, w = cur.shape[:2]
    if prev.shape != cur.shape:
        return 0.0
    x0, x1 = max(0, int(f.x * w)), min(w, int((f.x + f.w) * w))
    y0, y1 = max(0, int((f.y + f.h * 0.55) * h)), min(h, int((f.y + f.h) * h))
    if x1 - x0 < 4 or y1 - y0 < 3:
        return 0.0
    a = cv2.cvtColor(prev[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    b = cv2.cvtColor(cur[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.mean(np.abs(a - b)) / 255.0)


def spectral_saliency_center(img: np.ndarray) -> tuple[float, float, float]:
    """Spectral-residual saliency (Hou & Zhang 2007): returns (cx, cy, spread) normalized."""
    g = cv2.cvtColor(cv2.resize(img, (128, 72)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    f = np.fft.fft2(g)
    log_amp = np.log(np.abs(f) + 1e-6)
    phase = np.angle(f)
    resid = log_amp - cv2.blur(log_amp, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(resid + 1j * phase))) ** 2
    sal = cv2.GaussianBlur(sal, (9, 9), 2.5)
    sal = sal / (sal.sum() + 1e-9)
    ys, xs = np.mgrid[0:72, 0:128]
    cx, cy = float((sal * xs).sum() / 128), float((sal * ys).sum() / 72)
    spread = float(np.sqrt((sal * ((xs / 128 - cx) ** 2)).sum()))
    return cx, cy, spread


def smooth_path(times: np.ndarray, xs: np.ndarray, deadzone: float, max_speed: float, shot_breaks: list[int]) -> np.ndarray:
    """Deadzone + critically-damped follow + speed limit; hard cuts at shot breaks."""
    out = np.zeros_like(xs)
    if len(xs) == 0:
        return out
    pos = xs[0]
    vel = 0.0
    breaks = set(shot_breaks)
    for i in range(len(xs)):
        if i in breaks:
            pos, vel = xs[i], 0.0
        dt = times[i] - times[i - 1] if i > 0 else 0.0
        target = xs[i]
        if abs(target - pos) > deadzone and dt > 0:
            omega = 3.0
            err = target - pos
            acc = omega * omega * err - 2 * omega * vel
            vel += acc * dt
            vel = float(np.clip(vel, -max_speed, max_speed))
            pos += vel * dt
        else:
            vel *= 0.5
        out[i] = pos
    return out


def rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    (x0, y0), (x1, y1) = points[0], points[-1]
    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        x, y = points[i]
        if x1 == x0:
            d = abs(y - y0)
        else:
            yi = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
            d = abs(y - yi)
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return rdp(points[: idx + 1], eps)[:-1] + rdp(points[idx:], eps)
    return [points[0], points[-1]]


class ReframePlanner:
    def __init__(self, models_dir: Path | None = None, analysis_fps: float = 5.0) -> None:
        self.models_dir = models_dir
        self.fps = analysis_fps

    def plan(self, src: str | Path, start: float, end: float, mode: str = "auto", manual: dict[str, Any] | None = None,
             audio_energy=None, category: str | None = None) -> ReframePlan:
        info = probe_video(src)
        W, H = int(info["width"] or 1920), int(info["height"] or 1080)
        crop_h = H
        crop_w = int(round(H * 9 / 16 / 2) * 2)
        if W / H < 0.75:  # already vertical
            return ReframePlan("center", W, H, min(W, crop_w), min(H, int(W * 16 / 9)), [(0.0, 0.0)], 0, reason="source already vertical")
        if crop_w > W:
            crop_w = W
        if mode == "fit":
            return ReframePlan("fit", W, H, reason="manual: fit")
        if mode == "center":
            return ReframePlan("center", W, H, crop_w, crop_h, [(0.0, (W - crop_w) / 2)], reason="manual: center")
        if mode == "manual":
            m = manual or {}
            zoom = float(m.get("zoom", 1.0))
            cw = int(round(min(W, crop_w / max(1.0, zoom)) / 2) * 2)
            ch = int(round(min(H, crop_h / max(1.0, zoom)) / 2) * 2)
            cx = float(m.get("x", 0.5)) * W
            cy = float(m.get("y", 0.5)) * H
            x = float(np.clip(cx - cw / 2, 0, W - cw))
            y = int(np.clip(cy - ch / 2, 0, H - ch))
            return ReframePlan("track", W, H, cw, ch, [(0.0, x)], y, reason="manual framing")

        frames = decode_frames(src, fps=self.fps, width=480, start=start, end=end)
        det = FaceDetector(self.models_dir)
        tracks: list[Track] = []
        sal: list[tuple[float, float, float]] = []
        prev = None
        for fi, (t, img) in enumerate(frames):
            faces = [f for f in det.detect(img) if f.area > 0.0015]
            _associate(tracks, faces, fi)
            for tr in tracks:
                if fi in tr.boxes and prev is not None:
                    tr.activity[fi] = _mouth_motion(prev, img, tr.boxes[fi])
            sal.append(spectral_saliency_center(img))
            prev = img
        n = len(frames)
        times = np.array([t - start for t, _ in frames]) if frames else np.zeros(0)
        tracks = [t for t in tracks if len(t.boxes) >= max(3, 0.15 * n)]
        face_cov = (len({fi for t in tracks for fi in t.boxes}) / n) if n else 0.0
        stats = {"frames": n, "tracks": len(tracks), "face_coverage": round(face_cov, 2), "detector": det.backend}
        alternatives = ["fit", "center", "manual"]

        if mode in ("split", "auto"):
            cam = self._webcam_track(tracks, n)
            if cam is not None and (mode == "split" or category in ("gaming", None) or face_cov > 0.6):
                return self._split_webcam(W, H, cam, sal, stats, alternatives)
            if mode == "split" and len(tracks) >= 2:
                return self._split_two(W, H, tracks, stats, alternatives)

        if not tracks or face_cov < 0.3:
            spreads = [s[2] for s in sal]
            if not sal or (np.median(spreads) > 0.27 and mode == "auto"):
                return ReframePlan("fit", W, H, reason="contenido repartido por todo el encuadre (sin sujeto claro)",
                                   alternatives=["center", "track", "manual"], stats=stats)
            xs = np.array([s[0] * W for s in sal])
            path = smooth_path(times, xs, deadzone=0.06 * W, max_speed=0.35 * W, shot_breaks=[])
            return self._track_plan(W, H, crop_w, crop_h, times, path, "seguimiento por saliencia/movimiento", stats, alternatives)

        # face tracking with active-speaker selection
        energy = np.ones(n)
        if audio_energy is not None and n:
            energy = np.array([audio_energy(start + t) for t in times])
        focus = np.full(n, -1)
        act = np.zeros((len(tracks), n))
        for k, tr in enumerate(tracks):
            for fi, v in tr.activity.items():
                act[k, fi] = v * energy[fi]
            for fi in tr.boxes:
                act[k, fi] += 1e-4 * tr.boxes[fi].area  # tie-break: bigger face
        win = max(1, int(self.fps * 0.8))
        kernel = np.ones(win) / win
        smoothed = np.array([np.convolve(a, kernel, mode="same") for a in act]) if len(tracks) else act
        cur, since = -1, 0
        min_shot = int(self.fps * 1.5)
        breaks = []
        for fi in range(n):
            visible = [k for k, tr in enumerate(tracks) if fi in tr.boxes or any(abs(fi - j) <= 3 for j in tr.boxes)]
            if not visible:
                focus[fi] = cur
                continue
            best = max(visible, key=lambda k: smoothed[k, fi])
            if cur == -1:
                cur, since = best, fi
            elif best != cur and fi - since >= min_shot and smoothed[best, fi] > 1.4 * smoothed[cur, fi] + 1e-4:
                cur, since = best, fi
                breaks.append(fi)
            focus[fi] = cur
        xs = np.zeros(n)
        for fi in range(n):
            k = focus[fi]
            if k < 0:
                xs[fi] = xs[fi - 1] if fi else W / 2
                continue
            tr = tracks[k]
            near = min(tr.boxes, key=lambda j: abs(j - fi))
            xs[fi] = tr.boxes[near].cx * W
        path = smooth_path(times, xs, deadzone=0.05 * W, max_speed=0.45 * W, shot_breaks=breaks)
        stats["speaker_switches"] = len(breaks)
        reason = "seguimiento del hablante activo" if len(tracks) > 1 else "seguimiento del sujeto principal"
        if len(tracks) > 1:
            alternatives = ["split"] + alternatives
        return self._track_plan(W, H, crop_w, crop_h, times, path, reason, stats, alternatives, breaks)

    def _track_plan(self, W, H, crop_w, crop_h, times, path, reason, stats, alternatives, breaks=None) -> ReframePlan:
        xs = np.clip(path - crop_w / 2, 0, W - crop_w)
        pts = [(float(t), float(x)) for t, x in zip(times, xs)]
        # piecewise-linear simplification, keeping hard cuts as vertical steps
        segments: list[list[tuple[float, float]]] = []
        cur: list[tuple[float, float]] = []
        bset = set(breaks or [])
        for i, p in enumerate(pts):
            if i in bset and cur:
                segments.append(cur)
                cur = []
            cur.append(p)
        if cur:
            segments.append(cur)
        keyframes: list[tuple[float, float]] = []
        for seg in segments:
            simp = rdp(seg, eps=0.01 * W)
            keyframes.extend(simp)
        if not keyframes:
            keyframes = [(0.0, (W - crop_w) / 2)]
        keyframes = [(round(t, 3), round(x, 1)) for t, x in keyframes]
        return ReframePlan("track", W, H, crop_w, crop_h, keyframes, 0, reason=reason, alternatives=alternatives, stats=stats)

    @staticmethod
    def _webcam_track(tracks: list[Track], n: int) -> Track | None:
        """A small, static face in a corner while the rest of the frame changes → webcam overlay."""
        for tr in tracks:
            if len(tr.boxes) < 0.5 * n:
                continue
            cxs = np.array([f.cx for f in tr.boxes.values()])
            cys = np.array([f.cy for f in tr.boxes.values()])
            area = np.median([f.area for f in tr.boxes.values()])
            corner = (cxs.mean() < 0.33 or cxs.mean() > 0.67) and (cys.mean() < 0.4 or cys.mean() > 0.6)
            if area < 0.03 and corner and cxs.std() < 0.03 and cys.std() < 0.03:
                return tr
        return None

    def _split_webcam(self, W, H, cam: Track, sal, stats, alternatives) -> ReframePlan:
        f = sorted(cam.boxes.values(), key=lambda f: f.area)[len(cam.boxes) // 2]
        top_h = int(OUT_H * 0.38)
        bot_h = OUT_H - top_h
        cw = f.w * 3.2
        ch = cw * (top_h / OUT_W) * (W / H)
        cam_r = {"x": float(np.clip(f.cx - cw / 2, 0, 1 - cw)), "y": float(np.clip(f.cy - ch / 2, 0, 1 - min(1, ch))),
                 "w": float(min(1, cw)), "h": float(min(1, ch)), "out_h": top_h}
        game_w_px = H * OUT_W / bot_h
        gx = float(np.median([s[0] for s in sal])) if sal else 0.5
        gw = min(1.0, game_w_px / W)
        game_r = {"x": float(np.clip(gx - gw / 2, 0, 1 - gw)), "y": 0.0, "w": gw, "h": 1.0, "out_h": bot_h}
        return ReframePlan("split", W, H, regions=[cam_r, game_r], reason="webcam + gameplay detectados",
                           alternatives=["track", "fit"] + alternatives, stats=stats)

    def _split_two(self, W, H, tracks: list[Track], stats, alternatives) -> ReframePlan:
        two = sorted(tracks, key=lambda t: -len(t.boxes))[:2]
        two.sort(key=lambda t: np.median([f.cx for f in t.boxes.values()]))
        half = OUT_H // 2
        regions = []
        for tr in two:
            cx = float(np.median([f.cx for f in tr.boxes.values()]))
            cy = float(np.median([f.cy for f in tr.boxes.values()]))
            h = 1.0 * 0.8
            w = h * (OUT_W / half) * (H / W)
            regions.append({"x": float(np.clip(cx - w / 2, 0, 1 - w)), "y": float(np.clip(cy - h / 2, 0, 1 - h)), "w": w, "h": h, "out_h": half})
        return ReframePlan("split", W, H, regions=regions, reason="dos interlocutores apilados", alternatives=["track"] + alternatives, stats=stats)


def x_expression(keyframes: list[tuple[float, float]]) -> str:
    """Piecewise-linear FFmpeg expression of ``t`` for the crop x position."""
    if not keyframes:
        return "0"
    if len(keyframes) == 1:
        return f"{keyframes[0][1]:.1f}"
    expr = f"{keyframes[-1][1]:.1f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(keyframes, keyframes[1:]))):
        if t1 - t0 < 1e-3:
            seg = f"{x1:.1f}"
        else:
            slope = (x1 - x0) / (t1 - t0)
            seg = f"({x0:.1f}+({slope:.3f})*(t-{t0:.3f}))"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return f"if(lt(t,{keyframes[0][0]:.3f}),{keyframes[0][1]:.1f},{expr})"


def video_filter(plan: ReframePlan) -> str:
    """Filtergraph fragment from [vin] to [vframed] at 1080x1920."""
    if plan.layout == "fit":
        return ("[vin]split=2[bgsrc][fgsrc];"
                f"[bgsrc]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},boxblur=24:4,eq=brightness=-0.06[bg];"
                f"[fgsrc]scale={OUT_W}:-2:flags=lanczos[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vframed]")
    if plan.layout == "split" and plan.regions:
        W, H = plan.src_w, plan.src_h
        n = len(plan.regions)
        parts = [f"[vin]split={n}" + "".join(f"[s{i}]" for i in range(n)) + ";"]
        for i, r in enumerate(plan.regions):
            cw = int(round(r["w"] * W / 2) * 2)
            ch = int(round(r["h"] * H / 2) * 2)
            parts.append(f"[s{i}]crop={cw}:{ch}:{int(r['x'] * W)}:{int(r['y'] * H)},"
                         f"scale={OUT_W}:{int(r['out_h'])}:force_original_aspect_ratio=increase:flags=lanczos,"
                         f"crop={OUT_W}:{int(r['out_h'])}[r{i}];")
        parts.append("".join(f"[r{i}]" for i in range(n)) + f"vstack=inputs={n},setsar=1[vframed]")
        return "".join(parts)
    cw = plan.crop_w or int(plan.src_h * 9 / 16)
    ch = plan.crop_h or plan.src_h
    x = x_expression(plan.keyframes)
    return f"[vin]crop={cw}:{ch}:'{x}':{plan.y},scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1[vframed]"
