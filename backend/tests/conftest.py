from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipper.asr.engine import Word

FFMPEG = shutil.which("ffmpeg") is not None


def make_words(text: str, start: float = 0.0, wps: float = 3.0, gap_after_sentence: float = 0.6, spk: str | None = None) -> list[Word]:
    words, t = [], start
    for tok in text.split():
        d = 1.0 / wps
        words.append(Word(tok, t, t + d * 0.85, 0.95, spk))
        t += d
        if tok[-1] in ".!?":
            t += gap_after_sentence
    return words


@pytest.fixture
def tts_video(tmp_path: Path) -> Path:
    """Short real-speech video (flite TTS voice + test pattern)."""
    if not FFMPEG:
        pytest.skip("ffmpeg not installed")
    wav = tmp_path / "speech.wav"
    text = "So one time I was walking home. You will not believe what happened. A guy stopped me and asked if I was famous."
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"flite=text='{text}':voice=slt",
                    "-ar", "16000", "-ac", "1", str(wav)], check=True)
    out = tmp_path / "tts.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
                    "-i", str(wav), "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-c:a", "aac", str(out)], check=True)
    return out


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(42)
