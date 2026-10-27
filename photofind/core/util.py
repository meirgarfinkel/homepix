"""Small helpers shared by the whole app."""

import hashlib
import os
import sys
from pathlib import Path

import numpy as np


def is_tty() -> bool:
    return sys.stdout is not None and sys.stdout.isatty()


def say(msg=""):
    print(msg, flush=True)


def human_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-12)


def be_nice():
    """Run at low priority so the computer stays usable while indexing."""
    try:
        if os.name == "nt":
            import ctypes

            BELOW_NORMAL = 0x4000
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL)
        else:
            os.nice(10)
    except Exception:
        pass


def quick_hash(path: Path, size: int) -> str:
    """Fingerprint from file size + first/last 64 KB: spots moved or renamed photos."""
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(65536))
        if size > 131072:
            f.seek(-65536, os.SEEK_END)
            h.update(f.read(65536))
    return h.hexdigest()
