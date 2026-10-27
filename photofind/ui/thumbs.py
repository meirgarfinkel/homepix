"""Thumbnails for the grid: decoded on background threads, newest request first, kept in a memory
cache with a size budget.

They come from the core's thumbnail files (made while indexing); a missing one is made on the spot.
Each is decoded straight to the tile size and cropped square, so a tile costs only its own pixels.
"""

import threading
from collections import OrderedDict

from PySide6.QtCore import QObject, QRect, QSize, Signal
from PySide6.QtGui import QImage, QImageReader

from ..core import photo_thumb
from ..core.thumbs import thumbs_dir

MEMORY_BUDGET = 160 * 1024 * 1024  # bytes of decoded tiles kept in memory


class ThumbLoader(QObject):
    ready = Signal(str)  # qhash: its tile is in the cache now (or failed: get() returns None, failed() True)

    def __init__(self, workers: int = 3, budget: int = MEMORY_BUDGET):
        super().__init__()
        self._cache = OrderedDict()  # qhash -> QImage, least recently used first
        self._bytes = 0
        self._budget = budget
        self._failed = set()
        self._size = 0  # tile edge in device pixels
        self._queue = []  # [(qhash, path)], most wanted first
        self._busy = set()
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._stop = False
        self._threads = [threading.Thread(target=self._work, daemon=True, name=f"thumbs-{i}") for i in range(workers)]
        for t in self._threads:
            t.start()

    # --- UI thread ---------------------------------------------------------------------------------

    def set_size(self, px: int):
        """Tile edge in device pixels. Changing it drops the cache (the tiles are the wrong size)."""
        with self._lock:
            if px != self._size:
                self._size = px
                self._cache.clear()
                self._bytes = 0
                self._queue.clear()

    def get(self, qhash: str):
        with self._lock:
            img = self._cache.get(qhash)
            if img is not None:
                self._cache.move_to_end(qhash)
            return img

    def failed(self, qhash: str) -> bool:
        return qhash in self._failed

    def want(self, items):
        """items: [(qhash, path)] to load, most wanted first. Replaces any earlier wish list, so
        photos scrolled past are no longer loaded."""
        with self._lock:
            self._queue = [(q, p) for q, p in items if q not in self._cache and q not in self._busy
                           and q not in self._failed]
            self._queue.reverse()  # pop() from the end takes the most wanted
            if self._queue:
                self._wake.notify_all()

    def stop(self):
        with self._lock:
            self._stop = True
            self._wake.notify_all()

    # --- worker threads ----------------------------------------------------------------------------

    def _work(self):
        while True:
            with self._lock:
                while not self._queue and not self._stop:
                    self._wake.wait()
                if self._stop:
                    return
                qhash, path = self._queue.pop()
                size = self._size
                self._busy.add(qhash)
            img = None
            try:
                img = self._decode(qhash, path, size)
            except Exception:
                pass
            with self._lock:
                self._busy.discard(qhash)
                if size != self._size:
                    continue  # the tile size changed meanwhile
                if img is None:
                    self._failed.add(qhash)
                else:
                    self._cache[qhash] = img
                    self._bytes += img.sizeInBytes()
                    while self._bytes > self._budget and len(self._cache) > 1:
                        _, old = self._cache.popitem(last=False)
                        self._bytes -= old.sizeInBytes()
            self.ready.emit(qhash)

    @staticmethod
    def _decode(qhash: str, path: str, size: int):
        file = thumbs_dir("photos") / f"{qhash}.jpg"
        if not file.exists():
            file = photo_thumb(qhash, path)  # raises if the photo can't be read (e.g. its drive is out)
        reader = QImageReader(str(file))
        w, h = reader.size().width(), reader.size().height()
        if w <= 0 or h <= 0:
            return None
        scale = size / min(w, h)
        sw, sh = max(size, round(w * scale)), max(size, round(h * scale))
        reader.setScaledSize(QSize(sw, sh))
        reader.setScaledClipRect(QRect((sw - size) // 2, (sh - size) // 2, size, size))
        img = reader.read()
        return None if img.isNull() else img.convertToFormat(QImage.Format.Format_RGB32)
