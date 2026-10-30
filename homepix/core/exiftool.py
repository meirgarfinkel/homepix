"""ExifTool, the standard reader and writer of photo metadata, kept running in the background.

Starting ExifTool (a Perl program) takes about a quarter of a second, so one copy is started on first
use and kept open (-stay_open), and each request is a few milliseconds. The bundled app ships its own
copy with its own Perl (on Windows, ExifTool's official Windows build); from source, HOMEPIX_EXIFTOOL
or the system's exiftool is used.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path


class ExifToolMissing(RuntimeError):
    """ExifTool isn't there (only from source: the bundled app has its own)."""


def bundled() -> Path | None:
    """The app's own ExifTool and Perl (see packaging/bundle_exiftool.sh and .ps1), if there is one."""
    here = os.environ.get("HOMEPIX_EXIFTOOL_DIR") or (
        str(Path(sys._MEIPASS) / "exiftool") if getattr(sys, "frozen", False) else ""
    )
    if not here:
        return None
    here = Path(here)
    return here if (here / "run.pl").is_file() or (here / "exiftool.exe").is_file() else None


def command() -> list[str]:
    """How to start ExifTool."""
    if os.environ.get("HOMEPIX_EXIFTOOL"):
        return [os.environ["HOMEPIX_EXIFTOOL"]]
    if b := bundled():
        if (b / "exiftool.exe").is_file():
            return [str(b / "exiftool.exe")]
        return [str(b / "perl" / "bin" / "perl"), str(b / "run.pl")]
    found = shutil.which("exiftool")
    if found:
        return [found]
    raise ExifToolMissing("ExifTool isn't installed (on Debian or Ubuntu: sudo apt install libimage-exiftool-perl)")


def _arg(a: str) -> str:
    """One argument, as a line of ExifTool's argument file (C-string escapes allow any value)."""
    a = str(a)
    if "\n" in a or "\r" in a or "\\" in a or a != a.strip():
        return "#[CSTR]" + a.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    return a


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


class ExifTool:
    """One running ExifTool. Safe to use from several threads (requests take turns)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._n = 0
        self._err = []
        self._err_ready = threading.Condition()
        self._version = None

    def _start(self):
        env = dict(os.environ)
        if b := bundled():  # our own Perl, with its own libraries, and none of the system's settings
            for var in ("PERL5LIB", "PERL5OPT", "PERLLIB"):
                env.pop(var, None)
            env["LD_LIBRARY_PATH"] = str(b / "perl" / "so")
        self._proc = subprocess.Popen(
            command()
            + ["-stay_open", "True", "-@", "-", "-common_args", "-charset", "filename=UTF8", "-charset", "UTF8"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read_errors, args=(self._proc,), daemon=True, name="exiftool-err").start()

    def _read_errors(self, proc):
        for line in proc.stderr:
            with self._err_ready:
                self._err.append(line.decode("utf-8", "replace"))
                self._err_ready.notify_all()

    def run(self, args) -> tuple[bytes, str]:
        """Run ExifTool with these arguments: (what it printed, its warnings and errors)."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            self._n += 1
            mark = f"{{ready{self._n}}}"
            lines = [_arg(a) for a in args] + ["-echo4", mark, f"-execute{self._n}"]
            try:
                self._proc.stdin.write(("\n".join(lines) + "\n").encode("utf-8"))
                self._proc.stdin.flush()
                out = bytearray()
                end = mark.encode() + b"\n"
                crlf = mark.encode() + b"\r\n"  # (on Windows its lines end that way)
                while not (out.endswith(end) or out.endswith(crlf)):
                    chunk = self._proc.stdout.read1(1 << 16)
                    if not chunk:
                        raise RuntimeError("ExifTool stopped unexpectedly")
                    out += chunk
            except (OSError, RuntimeError):
                self._proc = None
                raise
            with self._err_ready:
                self._err_ready.wait_for(lambda: any(mark in e for e in self._err), timeout=10)
                errors = "".join(e for e in self._err if mark not in e)
                self._err.clear()
            return bytes(out[: -len(crlf if out.endswith(crlf) else end)]), errors

    def read(self, paths, tags=(), options=("-n",)) -> dict[str, dict]:
        """{path: {tag: value}} for these files. tags like "XMP-dc:Description" (all of them if none);
        values as numbers with -n. Files that can't be read are left out."""
        paths = [os.path.abspath(p) for p in paths]  # (never taken for an option: they start with / or C:)
        if not paths:
            return {}
        # ExifTool names each file its own way (with / on Windows): match them back to ours
        ours = {_path_key(p): p for p in paths}
        out, _ = self.run(["-j", "-G1", "-struct", *options, *[f"-{t}" for t in tags], *paths])
        try:
            found = json.loads(out.decode("utf-8") or "[]")
        except ValueError:
            return {}
        return {ours.get(_path_key(f), f): d for d in found if (f := d.pop("SourceFile", None))}

    def version(self) -> float:
        """ExifTool's version, e.g. 13.59 (asked once)."""
        if self._version is None:
            try:
                self._version = float(self.run(["-ver"])[0].decode().strip())
            except ValueError:
                self._version = 0.0
        return self._version

    def close(self):
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.stdin.write(b"-stay_open\nFalse\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    self._proc.kill()
            self._proc = None


_shared = None
_shared_lock = threading.Lock()


def exiftool() -> ExifTool:
    """The app's one ExifTool."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = ExifTool()
        return _shared


def available() -> bool:
    try:
        command()
        return True
    except ExifToolMissing:
        return False
