"""Keeping the index up to date in the background: one pass over every library folder at a time.

Only one HomePix indexes at a time, even with both apps open: a pass first takes a lock file in
the data folder, and if another app holds it, this one waits for the next pass.
"""

import os
import sys
import threading
import time
from pathlib import Path

from .ai import ModelDownloadError
from .config import data_dir
from .library import index_folder
from .settings import library_folders
from .util import be_nice


class _IndexLock:
    """An exclusive lock on a file, held across processes; released if the process dies."""

    def __init__(self):
        self.f = None

    def acquire(self) -> bool:
        f = open(data_dir() / "indexing.lock", "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self.f = f
        return True

    def release(self):
        if self.f is not None:
            if os.name == "nt":  # Windows wants it unlocked before closing; elsewhere closing is enough
                import msvcrt

                try:
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            self.f.close()
            self.f = None


class LibraryBusy(RuntimeError):
    """Another HomePix held the library for too long."""


class library_lock:
    """Hold the indexing lock for a file operation, so no index pass (in this app or another) runs
    while files move. Waits up to timeout seconds for one to let go."""

    def __init__(self, timeout: float = 30):
        self.timeout = timeout
        self._lock = _IndexLock()

    def __enter__(self):
        until = time.time() + self.timeout
        while not self._lock.acquire():
            if time.time() > until:
                raise LibraryBusy("Another HomePix is busy with the library. Try again in a moment.")
            time.sleep(0.1)
        return self

    def __exit__(self, *exc):
        self._lock.release()


class Indexer:
    """Runs index_folder over the library folders on a background thread.

    snapshot() returns the state as a dict:
      running, phase ("idle" | "scanning" | "loading" | "indexing" | "elsewhere"), folder, done, todo,
      eta (seconds or None), error (a message to show, or None), missing (photos kept that can't be
      found), changes (goes up by one after each pass that added, moved or removed photos: compare it to
      know when to reload), last_run (time or None).
    on_finished() is called on the indexing thread after each pass.
    """

    def __init__(self, on_finished=None):
        self.lock = threading.Lock()
        self.thread = None
        self.stop_flag = False
        self.again = False
        self.forget_missing = False  # next pass: also remove photos that can't be found
        self.on_finished = on_finished
        self.state = {"running": False, "phase": "idle", "done": 0, "todo": 0, "eta": None, "error": None,
                      "folder": None, "last_run": None, "missing": 0, "changes": 0}

    def start(self, forget_missing=False):
        with self.lock:
            self.forget_missing |= forget_missing
            if self.thread and self.thread.is_alive():
                self.again = True  # finish this pass, then go again (e.g. a folder was added)
                return
            self.stop_flag = False
            self.thread = threading.Thread(target=self._run, daemon=True, name="indexer")
            self.thread.start()

    def stop(self):
        self.stop_flag = True

    def stop_and_wait(self, timeout: float = 60):
        """Stop, and wait for the current photo to finish."""
        self.stop()
        t = self.thread
        if t is not None:
            t.join(timeout=timeout)

    def _progress(self, info):
        with self.lock:
            self.state.update(phase=info["phase"], done=info["done"], todo=info["todo"],
                              eta=info["eta"], folder=info["root"], running=True,
                              sharpen_done=info.get("sharpen_done", 0), sharpen_todo=info.get("sharpen_todo", 0),
                              sharpen_eta=info.get("sharpen_eta"))

    def _run(self):
        be_nice()
        lock = _IndexLock()
        if not lock.acquire():
            with self.lock:
                self.state.update(running=False, phase="elsewhere", error=None)
            return
        try:
            self._passes()
        finally:
            lock.release()

    def _passes(self):
        while True:
            self.again = False
            with self.lock:
                self.state.update(running=True, error=None, phase="scanning", done=0, todo=0, eta=None,
                                  folder=None)
                forget, self.forget_missing = self.forget_missing, False
            missing, changed = 0, False
            for folder in library_folders():
                if self.stop_flag:
                    break
                if not Path(folder).is_dir():
                    continue  # e.g. an unplugged drive; its photos stay browsable and searchable
                try:
                    info = index_folder(folder, True, self._progress, lambda: self.stop_flag or self.again,
                                        forget_missing=forget)
                    missing += info["missing"]
                    changed |= bool(info["done"] or info["moved"] or info["removed"])
                except ModelDownloadError as e:
                    with self.lock:
                        self.state["error"] = (
                            "Couldn't download what HomePix needs to recognize photos. "
                            "Check your internet connection, then choose Check for new photos in Settings."
                        )
                    print(e, file=sys.stderr)
                    break
                except Exception as e:
                    with self.lock:
                        self.state["error"] = f"Something went wrong while looking through {folder}: {e}"
                    print(f"index error: {e!r}", file=sys.stderr)
            with self.lock:
                self.state["missing"] = missing
                self.state["changes"] += changed
            if self.on_finished:
                self.on_finished()
            if not self.again or self.stop_flag:
                break
        with self.lock:
            self.state.update(running=False, phase="idle", last_run=time.time())

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)
