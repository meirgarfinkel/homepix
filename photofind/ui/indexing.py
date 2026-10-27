"""Background indexing for the app: when it runs, and its progress in the status bar."""

import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QToolButton, QWidget

from ..core import Indexer, library_folders

RESCAN_EVERY = 30 * 60  # look for new photos every 30 minutes while the app is open
REFRESH_EVERY = 30  # while photos are being added, show the new ones this often (seconds)


class IndexWatcher(QObject):
    """Owns the indexer and tells the window what it's doing.

    state(dict): the indexer's snapshot, twice a second while it runs (and once when it stops).
    library_changed(): photos were added, moved or removed: time to reload what's shown.
    """

    state = Signal(dict)
    library_changed = Signal()

    def __init__(self, parent=None, rescan: bool = True):
        super().__init__(parent)
        self.indexer = Indexer()
        self._closed = False
        self._held = 0  # file operations under way: no indexing until they're done
        self._wanted = None  # a start asked for while held: forget_missing
        self._changes = 0
        self._was_running = False
        self._refreshed = time.time()
        self._shown_done = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)
        self._rescan = QTimer(self)
        self._rescan.timeout.connect(self.start)
        if rescan:
            self._rescan.start(RESCAN_EVERY * 1000)

    def start(self, forget_missing=False):
        if self._held:
            self._wanted = bool(self._wanted) or forget_missing
            return
        if library_folders() and not self._closed:
            self.indexer.start(forget_missing=forget_missing)
            self._tick()

    def stop(self):
        self.indexer.stop()

    def hold(self):
        """Keep indexing off until release() (call from the UI thread); the caller then waits for a pass
        under way with indexer.stop_and_wait()."""
        self._held += 1
        self.indexer.stop()

    def release(self, rescan: bool = True):
        """Indexing may run again; rescan: look for new photos now (e.g. files pasted in from elsewhere)."""
        self._held = max(0, self._held - 1)
        if not self._held:
            wanted, self._wanted = self._wanted, None
            if rescan or wanted is not None:
                self.start(forget_missing=bool(wanted))

    def shutdown(self, wait: float = 3.0):
        """Stop for good, waiting a little for the current photo to finish (the index is safe either way)."""
        self._closed = True
        self._timer.stop()
        self._rescan.stop()
        self.indexer.stop()
        t = self.indexer.thread
        if t is not None:
            t.join(timeout=wait)

    def _tick(self):
        s = self.indexer.snapshot()
        thread = self.indexer.thread
        running = s["running"] or (thread is not None and thread.is_alive())
        if running or self._was_running:
            self.state.emit(s)
        # new photos show up while a long pass is still going, not only at its end
        if running and s["phase"] == "indexing" and s["done"] > self._shown_done \
                and time.time() - self._refreshed > REFRESH_EVERY:
            self._shown_done = s["done"]
            self._refresh()
        if s["changes"] != self._changes:
            self._changes = s["changes"]
            self._refresh()
        if not running:
            self._shown_done = 0
        self._was_running = running

    def _refresh(self):
        self._refreshed = time.time()
        self.library_changed.emit()


def time_left(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return "less than a minute left"
    if minutes < 90:
        return f"about {minutes} minute{'s' if minutes != 1 else ''} left"
    return f"about {round(minutes / 60)} hours left"


def describe(s: dict) -> str:
    """What the indexer is doing, in a few words for the status bar."""
    if s["phase"] == "elsewhere":
        return "Another Photo Finder window is updating the library."
    if not s["running"]:
        return ""
    if s["phase"] == "loading":
        return "Getting ready to look through your photos..."
    if s["phase"] == "indexing" and s["todo"]:
        text = f"Adding photos: {s['done']:,} of {s['todo']:,}"
        if s["eta"] and s["done"] >= 20:  # the speed settles after the first few
            text += f", {time_left(s['eta'])}"
        return text
    return "Looking for new photos..."


class IndexStatus(QWidget):
    """In the status bar: what indexing is doing, how far it got, and a button to stop it."""

    def __init__(self, watcher: IndexWatcher, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 6, 0)
        self.label = QLabel()
        self.bar = QProgressBar()
        self.bar.setFixedWidth(160)
        self.bar.setTextVisible(False)
        self.stop = QToolButton()
        self.stop.setText("Stop")
        self.stop.setToolTip("Stop for now; it carries on where it left off next time")
        self.stop.clicked.connect(watcher.stop)
        for w in (self.label, self.bar, self.stop):
            lay.addWidget(w)
        watcher.state.connect(self.show_state)
        self.show_state({"running": False, "phase": "idle"})

    def show_state(self, s: dict):
        text = describe(s)
        self.label.setText(text)
        self.label.setVisible(bool(text))
        busy = bool(s["running"])
        self.bar.setVisible(busy)
        self.stop.setVisible(busy)
        if busy:
            if s["phase"] == "indexing" and s.get("todo"):
                self.bar.setRange(0, s["todo"])
                self.bar.setValue(s["done"])
            else:
                self.bar.setRange(0, 0)  # busy, amount unknown
