"""Small helpers shared by the whole app."""

import contextlib
import hashlib
import json
import os
import shutil
import ssl
import sys
import threading
import time
from pathlib import Path

import numpy as np


def say(msg=""):
    """A line for the app's log (app.log when started from the menu)."""
    print(msg, flush=True)


# Where Linux and macOS keep the certificates they trust. The OpenSSL bundled in the Linux builds was made
# on AlmaLinux, so it only looks where Red Hat keeps them (/etc/pki/tls): on Ubuntu or Debian it would
# trust no one, and every download would fail.
CA_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Mint, Arch
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL, Alma, Rocky
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # (the same, newer)
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/ssl/cert.pem",  # macOS, Alpine
)


def https_context() -> ssl.SSLContext:
    """For downloads over HTTPS (models, updates): the system's own trusted certificates, wherever this
    system keeps them. Windows' come from Windows itself."""
    ctx = ssl.create_default_context()
    if sys.platform != "win32" and not ctx.get_ca_certs():
        for f in CA_FILES:
            if os.path.isfile(f):
                try:
                    ctx.load_verify_locations(cafile=f)
                    break
                except (OSError, ssl.SSLError):
                    continue
    return ctx


def folder_prefix(folder) -> str:
    """folder with a separator after it: what the paths inside it start with."""
    return str(folder).rstrip(os.sep) + os.sep


def within(path, folders) -> bool:
    """Is path one of folders, or inside one?"""
    path = os.path.abspath(path)
    return any(path == f or path.startswith(folder_prefix(f)) for f in map(os.path.abspath, folders))


def unit_vectors(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-12)


def lower_priority():
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


_writing = threading.RLock()


def write_atomic(path: Path, text: str):
    """Write a small text file whole or not at all. Each write has its own temporary file, so two at
    once (from different threads) never trip over each other; and on Windows, where a file that's being
    read can't be replaced for that moment, it tries again."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with _writing:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())  # (on disk before it takes the old one's place: a power cut leaves one or the other)
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(0.05)


_DAMAGED = object()


def _json_in(path: Path):
    """What a JSON file holds; None if there's no file; _DAMAGED if it can't be read (tried a few times:
    on Windows another program can hold it for a moment)."""
    for attempt in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except ValueError:
            return _DAMAGED
        except OSError:
            time.sleep(0.05 * (attempt + 1))
    return _DAMAGED


def load_json(path: Path, default):
    """The JSON in path, or default when there's none. A damaged file is never taken for "none" (and
    then saved over, losing what it held): the copy kept by the save before (path.bak) is used instead,
    and the damaged file is kept aside beside it."""
    got = _json_in(path)
    if got is None:
        return default
    if got is not _DAMAGED:
        return got
    with _writing:
        got = _json_in(path)  # (another thread may have just saved it)
        if got is not _DAMAGED:
            return default if got is None else got
        kept = _json_in(path.with_name(path.name + ".bak"))
        with contextlib.suppress(OSError):
            path.replace(path.with_name(f"{path.stem}-unreadable-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}"))
        if kept is None or kept is _DAMAGED:
            return default
        write_atomic(path, json.dumps(kept, indent=2))
        return kept


def save_json(path: Path, value):
    """Write value to path whole, keeping the last good one as path.bak for load_json to fall back on."""
    with _writing:
        if _json_in(path) not in (None, _DAMAGED):
            with contextlib.suppress(OSError):
                shutil.copyfile(path, path.with_name(path.name + ".bak"))
        write_atomic(path, json.dumps(value, indent=2))
