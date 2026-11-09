"""Clip Generator — final render: reframe + animated captions + title + watermark → 1080x1920 H.264/AAC."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clipper.captions.ass import CaptionRenderer
from clipper.captions.styles import FONTS_DIR
from clipper.core.system import nvenc_available
from clipper.reframe.planner import OUT_H, OUT_W, ReframePlan, video_filter

log = logging.getLogger(__name__)


class RenderError(Exception):
    pass


@dataclass
class RenderSpec:
    source: Path
    output: Path
    trim_start: float  # seconds within the source file
    trim_end: float
    words: list[dict[str, Any]]  # times relative to the source file
    caption_style: dict[str, Any]
    plan: ReframePlan
    title: dict[str, Any] | None = None
    watermark: dict[str, Any] | None = None
    crf: int = 18
    preset: str = "medium"
    audio_bitrate: str = "192k"
    fps: int = 30
    normalize_audio: bool = True
    preview: bool = False  # fast low-res preview

    @property
    def duration(self) -> float:
        return max(0.1, self.trim_end - self.trim_start)


def _filter_escape(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def build_command(spec: RenderSpec, ass_path: Path, use_nvenc: bool = False) -> list[str]:
    dur = spec.duration
    vf = video_filter(spec.plan)
    chain = f"[0:v]fps={spec.fps},format=yuv420p[vin];{vf};[vframed]ass='{_filter_escape(ass_path)}':fontsdir='{_filter_escape(FONTS_DIR)}'"
    wm = spec.watermark or {}
    inputs = ["-ss", f"{spec.trim_start:.3f}", "-t", f"{dur:.3f}", "-i", str(spec.source)]
    if wm.get("image") and Path(wm["image"]).exists():
        inputs += ["-i", str(wm["image"])]
        pos = {"top-left": "40:60", "top-right": "W-w-40:60", "bottom-left": "40:H-h-60", "bottom-right": "W-w-40:H-h-60"}
        op = float(wm.get("opacity", 0.7))
        chain += (f"[vsub];[1:v]scale={int(OUT_W * float(wm.get('scale', 0.22)))}:-1,format=rgba,colorchannelmixer=aa={op}[wm];"
                  f"[vsub][wm]overlay={pos.get(wm.get('position', 'top-right'), 'W-w-40:60')}")
    if spec.preview:
        chain += ",scale=540:960"
    chain += "[vout]"
    afilter = "aresample=48000"
    if spec.normalize_audio and not spec.preview:
        afilter = "loudnorm=I=-14:TP=-1.5:LRA=11," + afilter
    chain += f";[0:a]{afilter}[aout]"
    cmd = ["-hide_banner", "-nostdin", "-y", *inputs, "-filter_complex", chain, "-map", "[vout]", "-map", "[aout]?"]
    if use_nvenc and not spec.preview:
        cmd += ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(spec.crf + 1), "-b:v", "0", "-profile:v", "high"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast" if spec.preview else spec.preset,
                "-crf", str(spec.crf + (8 if spec.preview else 0)), "-profile:v", "high", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", spec.audio_bitrate, "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats", str(spec.output)]
    return cmd


async def render(spec: RenderSpec, work_dir: Path, on_progress: Callable[[float], None] | None = None,
                 has_audio: bool = True) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    spec.output.parent.mkdir(parents=True, exist_ok=True)
    ass = CaptionRenderer(OUT_W, OUT_H).build(spec.words, offset=spec.trim_start, duration=spec.duration,
                                              style=spec.caption_style, title=spec.title, watermark=spec.watermark)
    ass_path = work_dir / f"{spec.output.stem}.ass"
    ass_path.write_text(ass, encoding="utf-8")
    use_nvenc = nvenc_available()
    cmd = build_command(spec, ass_path, use_nvenc)
    if not has_audio:
        i = cmd.index("-filter_complex") + 1
        cmd[i] = cmd[i].split(";[0:a]")[0]
        cmd = [c for c in cmd if c != "[aout]?"]
        j = cmd.index("-c:a")
        del cmd[j: j + 2]
    log.info("render: ffmpeg %s", " ".join(cmd)[:600])
    t0 = time.time()
    # lower the render priority so capture/analysis stay responsive (`nice` does not exist on Windows)
    prefix = ["nice", "-n", "10"] if shutil.which("nice") else []
    proc = await asyncio.create_subprocess_exec(*prefix, "ffmpeg", *cmd, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    assert proc.stdout and proc.stderr
    err_task = asyncio.create_task(proc.stderr.read())
    last = 0.0
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        m = re.match(rb"out_time_(?:us|ms)=(\d+)", line)
        if m and on_progress:
            frac = min(1.0, int(m.group(1)) / 1e6 / spec.duration)
            if frac - last >= 0.02:
                last = frac
                on_progress(frac)
    await proc.wait()
    err = (await err_task).decode(errors="replace")
    if proc.returncode != 0 or not spec.output.exists():
        raise RenderError(err.strip()[-1500:] or f"ffmpeg exited with {proc.returncode}")
    if on_progress:
        on_progress(1.0)
    return {"seconds": round(time.time() - t0, 2), "encoder": "h264_nvenc" if use_nvenc else "libx264",
            "ass": str(ass_path), "duration": spec.duration}
