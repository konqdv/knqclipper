"""Host resource metrics (CPU / RAM / GPU / disk) and hardware capability detection."""

from __future__ import annotations

import logging
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import psutil

log = logging.getLogger(__name__)


@lru_cache
def cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


@lru_cache
def nvenc_available() -> bool:
    if not cuda_available() or not shutil.which("ffmpeg"):
        return False
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=20,
        )
        return out.returncode == 0
    except Exception:
        return False


def gpu_stats() -> list[dict]:
    try:
        import pynvml  # provided by nvidia-ml-py (optional)

        pynvml.nvmlInit()
        res = []
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            name = pynvml.nvmlDeviceGetName(h)
            res.append({
                "index": i,
                "name": name.decode() if isinstance(name, bytes) else name,
                "util_percent": util.gpu,
                "mem_used_mb": mem.used // 2**20,
                "mem_total_mb": mem.total // 2**20,
            })
        if res:
            return res
    except Exception:
        pass
    # fallback: query nvidia-smi directly (no extra Python dependency needed)
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            res = []
            for line in out.stdout.strip().splitlines():
                p = [x.strip() for x in line.split(",")]
                if len(p) >= 5:
                    res.append({"index": int(p[0]), "name": p[1], "util_percent": int(float(p[2])),
                                "mem_used_mb": int(float(p[3])), "mem_total_mb": int(float(p[4]))})
            return res
    except Exception:
        pass
    return []


def snapshot(data_dir: Path) -> dict:
    vm = psutil.virtual_memory()
    du = shutil.disk_usage(data_dir)
    proc = psutil.Process()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "load_avg": list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else None,
        "ram_percent": vm.percent,
        "ram_used_mb": (vm.total - vm.available) // 2**20,
        "ram_total_mb": vm.total // 2**20,
        "process_rss_mb": proc.memory_info().rss // 2**20,
        "disk_free_gb": round(du.free / 2**30, 2),
        "disk_total_gb": round(du.total / 2**30, 2),
        "gpus": gpu_stats(),
        "cuda": cuda_available(),
    }
