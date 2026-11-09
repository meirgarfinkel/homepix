"""Running slow work (disk, decoding, EXIF) off the UI thread, with results delivered back to it."""

import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal


class _Relay(QObject):
    done = Signal(object, object, object)  # callback, result, error


_relay = None
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="homepix-job")
# Changes to files (which may wait for indexing to pause) and the viewer's decoding each have their own
# threads, so neither waits behind the other or behind thumbnails and lists.
_changes = ThreadPoolExecutor(max_workers=1, thread_name_prefix="homepix-change")
_viewing = ThreadPoolExecutor(max_workers=2, thread_name_prefix="homepix-view")
_pending = 0  # jobs submitted to the pool and not finished yet
_vital = 0  # of those, the ones quitting must wait for
_idle = threading.Condition()


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


def _counted(job, must_finish):
    def run_it():
        global _pending, _vital
        try:
            job()
        finally:
            with _idle:
                _pending -= 1
                _vital -= must_finish
                _idle.notify_all()

    return run_it


def run(fn, callback, *args, must_finish=False, viewing=False):
    """Run fn(*args) on a worker thread, then callback(result, error) on the UI thread.
    error is None on success, else the exception (and result None). must_finish: quitting waits for
    it however long it takes (changes to the user's files). viewing: decoding for the viewer."""
    global _pending, _vital
    with _idle:
        _pending += 1
        _vital += must_finish
    pool = _changes if must_finish else _viewing if viewing else _pool
    return pool.submit(_counted(_job(fn, callback, args), must_finish))


def wait_idle(timeout: float) -> bool:
    """On quitting: wait for jobs that must finish, then up to timeout seconds for the rest. True if
    every job finished."""
    with _idle:
        _idle.wait_for(lambda: _vital == 0)
        return _idle.wait_for(lambda: _pending == 0, timeout)


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
