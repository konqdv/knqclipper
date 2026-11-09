"""Capture for non-HLS inputs (local files, VOD URLs, RTMP/SRT ingest) using FFmpeg's segment muxer.

FFmpeg stream-copies the input into MPEG-TS segments and reports each finished segment on a CSV
segment list written to stdout, which gives exact start/end times without re-encoding.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from clipper.buffer.rolling import Segment
from clipper.capture.base import CaptureError, CaptureSource, SegmentCallback, StreamEnded

log = logging.getLogger(__name__)


class FFmpegSegmenterCapture(CaptureSource):
    def __init__(
        self,
        out_dir: Path,
        input_url: str,
        *,
        realtime: bool = True,
        segment_seconds: float = 2.0,
        listen: bool = False,
        input_args: list[str] | None = None,
        reencode: bool = False,
        start_offset: float = 0.0,
    ) -> None:
        super().__init__(out_dir)
        self.input_url = input_url
        self.realtime = realtime
        self.segment_seconds = segment_seconds
        self.listen = listen
        self.input_args = input_args or []
        self.reencode = reencode
        self.start_offset = start_offset
        self._proc: asyncio.subprocess.Process | None = None
        self._index = 0
        self._time_base = 0.0  # added when an ingest connection restarts
        self._wall_start: float | None = None

    @property
    def wall_start(self) -> float | None:
        return self._wall_start

    def _cmd(self, run: int) -> list[str]:
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning"]
        if self.realtime and not self.listen:
            cmd += ["-re"]
        if self.start_offset and run == 0:
            cmd += ["-ss", f"{self.start_offset:.3f}"]
        if self.listen:
            cmd += ["-listen", "1"] if self.input_url.startswith("rtmp") else []
            cmd += ["-timeout", "0"] if self.input_url.startswith("rtmp") else []
        cmd += self.input_args + ["-i", self.input_url, "-map", "0:v:0?", "-map", "0:a:0?"]
        if self.reencode:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-g", "60",
                    "-force_key_frames", f"expr:gte(t,n_forced*{self.segment_seconds})", "-c:a", "aac", "-b:a", "160k"]
        else:
            cmd += ["-c", "copy"]
        pattern = str(self.out_dir / f"r{run:02d}_seg_%07d.ts")
        cmd += [
            "-f", "segment", "-segment_time", str(self.segment_seconds), "-segment_format", "mpegts",
            "-segment_list", "pipe:1", "-segment_list_type", "csv", "-reset_timestamps", "1", pattern,
        ]
        return cmd

    async def run(self, on_segment: SegmentCallback) -> None:
        run = 0
        while not self._stopped:
            ended_cleanly = await self._run_once(run, on_segment)
            if not self.listen:
                if ended_cleanly:
                    raise StreamEnded("input finished")
                raise CaptureError("ffmpeg failed reading the input")
            # ingest: wait for the encoder to reconnect, keep timeline continuous
            run += 1
            self.stats.reconnects += 1
            await asyncio.sleep(1.0)

    async def _run_once(self, run: int, on_segment: SegmentCallback) -> bool:
        cmd = self._cmd(run)
        log.info("capture ffmpeg: %s", " ".join(cmd))
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        assert self._proc.stdout is not None and self._proc.stderr is not None
        stderr_task = asyncio.create_task(self._proc.stderr.read())
        run_base = self._time_base
        last_end = run_base
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                parts = line.decode().strip().split(",")
                if len(parts) < 3:
                    continue
                name, s0, s1 = parts[0], float(parts[1]), float(parts[2])
                path = self.out_dir / Path(name).name
                if not path.exists():
                    continue
                if self._wall_start is None:
                    self._wall_start = time.time() - (s1 - s0)
                start = max(run_base + s0, last_end)
                dur = max(0.04, s1 - s0)
                seg = Segment(index=self._index, start=start, duration=dur, path=path, discontinuity=(run > 0 and self._index > 0 and start == run_base))
                self._index += 1
                last_end = seg.end
                self.stats.segments += 1
                self.stats.bytes += path.stat().st_size
                self.stats.last_segment_at = time.time()
                await on_segment(seg)
                if self._stopped:
                    break
        finally:
            if self._proc.returncode is None:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), 5)
                except asyncio.TimeoutError:
                    self._proc.kill()
            await self._proc.wait()
            err = (await stderr_task).decode(errors="replace").strip()
            if err:
                log.info("capture ffmpeg stderr: %s", err[-800:])
        self._time_base = last_end
        return self._proc.returncode == 0 or self._stopped

    def stop(self) -> None:
        super().stop()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
