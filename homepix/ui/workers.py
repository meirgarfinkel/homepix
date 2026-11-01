"""Running slow work (disk, decoding, EXIF) off the UI thread, with results delivered back to it."""

import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal


class _Relay(QObject):
    done = Signal(object, object, object)  # callback, result, error


_relay = None
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="homepix-job")


def _job(fn, callback, args):
    """The work, then its result to the UI thread. Made there: the relay delivers on its thread."""
    global _relay
    if _relay is None:
        _relay = _Relay()
        _relay.done.connect(lambda cb, res, err: cb(res, err))
    relay = _relay

    def job():
        try:
            res, err = fn(*args), None
        except Exception as e:  # handed to the callback, which decides what to show
            res, err = None, e
        relay.done.emit(callback, res, err)

    return job


def run(fn, callback, *args):
    """Run fn(*args) on a worker thread, then callback(result, error) on the UI thread.
    error is None on success, else the exception (and result None)."""
    return _pool.submit(_job(fn, callback, args))


def run_apart(fn, callback, *args):
    """Like run, on a thread of its own that doesn't hold up the shared ones or quitting
    (for long downloads)."""
    threading.Thread(target=_job(fn, callback, args), daemon=True).start()


class Latest:
    """For requests where only the newest answer matters (e.g. the info of the photo now selected):
    answers to older requests are dropped."""

    def __init__(self):
        self._n = 0
        self._lock = threading.Lock()

    def run(self, fn, callback, *args):
        with self._lock:
            self._n += 1
            n = self._n
        run(fn, lambda res, err: n == self._n and callback(res, err), *args)
