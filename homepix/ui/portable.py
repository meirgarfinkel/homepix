"""The library folders' portable copies, from the app: kept up to date in the background, and offered
when a folder that has one is added (see core/portable.py)."""

import datetime as dt
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox

from ..core import portable
from . import workers

TICK = 5  # seconds between looks at whether the database changed
QUIET = 5  # write once nothing has changed for this long...
GAP = 30  # ...and not more often than this
INDEXING_GAP = 5 * 60  # while indexing (changes all the time), this often


class PortableCopies(QObject):
    """Writes the copies a little after the database changes, in the background, and on close."""

    def __init__(self, window, active: bool = True):
        super().__init__(window)
        self.w = window
        self.active = active
        self._seen = None  # the database's stamp when last looked at
        self._changed_at = 0.0
        self._written = None  # its stamp when the copies were last brought up to date
        self._last_run = 0.0
        self._busy = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if active:
            self._timer.start(TICK * 1000)

    def _indexing(self) -> bool:
        return bool(self.w.watcher.indexer.snapshot()["running"])

    def _tick(self):
        if self._busy:
            return
        s = portable.stamp()
        now = time.time()
        if s != self._seen:
            self._seen, self._changed_at = s, now
        if s == self._written:
            return
        if self._indexing():
            if now - self._last_run < INDEXING_GAP:
                return
        elif now - self._changed_at < QUIET or now - self._last_run < GAP:
            return
        self._busy, self._last_run = True, now

        def done(_, err):
            self._busy = False
            if err is None:
                self._written = s
            else:
                print(f"Couldn't save the library's copies: {err!r}", flush=True)

        workers.run(portable.refresh_copies, done)

    def flush(self):
        """Bring every copy up to date now (on closing). Waits for one being written in the background."""
        self._timer.stop()
        if self.active:
            portable.refresh_copies()


def ask_use_copy(parent, root: str, meta: dict) -> bool:
    """Ask whether to bring in a folder's copy. True: use it."""
    try:
        d = dt.datetime.fromisoformat(meta.get("written", ""))
        when = f"{d.day} {d:%B %Y}"
    except ValueError:
        when = "an earlier day"
    people = meta["people"]
    box = QMessageBox(parent)
    box.setWindowTitle("HomePix library found")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(f"<b>{root} has a HomePix library in it.</b>")
    box.setInformativeText(
        f"It was saved on {when}, with {meta['photos']:,} photos"
        + (f" and {people:,} {'person' if people == 1 else 'people'} named" if people else "")
        + ".\n\n"
        "Use it to bring back the faces and names, search and duplicate choices, without looking through "
        "every photo again.\n\nStart fresh instead and HomePix looks through the photos from the "
        "beginning (the library found is kept in the folder)."
    )
    use = box.addButton("Use it", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Start fresh", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(use)
    box.exec()
    return box.clickedButton() is use


def offer(window, roots, then=None):
    """Bring in the copies of those of roots that have one waiting (asking about each), then call then().
    Indexing waits meanwhile, so it doesn't start on a folder from scratch first."""
    roots = [str(r) for r in roots]
    watcher = window.watcher
    watcher.hold()

    def finish():
        watcher.release(rescan=False)
        if then is not None:
            then()

    def find():
        watcher.indexer.stop_and_wait()
        return [(r, m) for r in roots if (m := portable.offered(r)) is not None]

    def found(res, err):
        if err is not None or not res:
            if err is not None:
                print(f"Couldn't look for a library's copy: {err!r}", flush=True)
            finish()
            return
        chosen = [r for r, m in res if ask_use_copy(window, r, m)]
        if not chosen:
            finish()
            return
        window.statusBar().showMessage("Bringing in your library: faces, names and search...")

        def done(got, err):
            if err is not None:
                QMessageBox.warning(window, "HomePix library", f"Couldn't bring the library in:\n{err}")
                window.statusBar().clearMessage()
            else:
                photos = sum(g["photos"] for _, g in got)
                people = sum(g["people"] for _, g in got)
                missing = sum(g["missing"] for _, g in got)
                window.statusBar().showMessage(
                    f"Brought in {photos:,} photos"
                    + (f" and {people:,} {'person' if people == 1 else 'people'}" if people else "")
                    + (f" ({missing:,} weren't in the folder any more)" if missing else "")
                    + ".",
                    10000,
                )
            window.library_brought_in()
            finish()

        workers.run(lambda: [(r, portable.read_copy(r)) for r in chosen], done)

    workers.run(find, found)
