"""Extract the "clip original" from rolling-buffer segments without re-encoding."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from clipper.buffer.rolling import Segment

log = logging.getLogger(__name__)


class ExtractError(Exception):
    pass


async def run_ffmpeg(args: list[str], timeout: float = 600) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec("ffmpeg", "-hide_banner", "-nostdin", "-y", *args,
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise ExtractError("ffmpeg timed out")
    return proc.returncode or 0, err.decode(errors="replace")


async def extract_segments(segments: list[Segment], out: Path, work_dir: Path) -> tuple[float, float]:
    """Concatenate segments (stream copy) into an MP4. Returns (stream start time, duration)."""
    if not segments:
        raise ExtractError("no segments available for this range (evicted or not captured)")
    out.parent.mkdir(parents=True, exist_ok=True)
    lst = work_dir / f"{out.stem}.concat.txt"
    lst.write_text("".join(f"file '{s.path.as_posix()}'\n" for s in segments))
    code, err = await run_ffmpeg(["-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                                  "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", str(out)])
    if code != 0 or not out.exists():
        log.warning("stream-copy extraction failed (%s); re-encoding", err.strip()[-300:])
        code, err = await run_ffmpeg(["-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                                      "-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
                                      "-crf", "16", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
        if code != 0:
            raise ExtractError(err.strip()[-500:])
    lst.unlink(missing_ok=True)
    start = segments[0].start
    return start, segments[-1].end - start


async def thumbnail(src: Path, t: float, out: Path, width: int = 480) -> Path | None:
    out.parent.mkdir(parents=True, exist_ok=True)
    code, _ = await run_ffmpeg(["-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1",
                                "-vf", f"scale={width}:-2", "-q:v", "4", str(out)])
    return out if code == 0 and out.exists() else None
