"""Thumbnails for the grid: decoded on background threads, newest request first, kept in a memory
cache with a size budget.

They come from the core's thumbnail files (made while indexing); a missing one is made on the spot.
An old, smaller one is shown until its sharper one has been made, then swapped in.
Each is cropped square and shrunk to the tile size, so a tile in memory costs only its own pixels.
"""

import contextlib
import threading
from collections import OrderedDict

from PIL import Image
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from ..core.thumbs import old_thumb_file, photo_thumb, thumb_file

MEMORY_BUDGET = 160 * 1024 * 1024  # bytes of decoded tiles kept in memory


def to_qimage(img) -> QImage:
    """A Pillow image as a QImage (a copy: img needn't outlive it)."""
    img = img.convert("RGB")
    return QImage(img.tobytes(), img.width, img.height, 3 * img.width, QImage.Format.Format_RGB888).copy()


class ThumbLoader(QObject):
    ready = Signal(str)  # qhash: its tile is in the cache now (or failed: get() returns None, failed() True)

    def __init__(self, workers: int = 3, budget: int = MEMORY_BUDGET):
        super().__init__()
        self._cache = OrderedDict()  # qhash -> QImage, least recently used first
        self._bytes = 0
        self._budget = budget
        self._failed = set()
        self._soft = set()  # qhashes whose tile came from an old thumbnail, until the sharper one's in
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
                self._soft.clear()
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
        photos scrolled past are no longer loaded. Copies with the same content (the same qhash) share
        one tile: it's loaded once."""
        with self._lock:
            seen = set()
            sharper = {q for q, _ in items if q in self._soft and thumb_file(q).exists()}  # (kept till then)
            self._queue = [
                (q, p)
                for q, p in items
                if (q not in self._cache or q in sharper)
                and q not in self._busy
                and q not in self._failed
                and not (q in seen or seen.add(q))
            ]
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
                if (qhash in self._cache and qhash not in self._soft) or qhash in self._busy:
                    continue  # (loaded meanwhile)
                size = self._size
                self._busy.add(qhash)
            img, soft = None, False
            with contextlib.suppress(Exception):
                img, soft = self._decode(qhash, path, size)
            with self._lock:
                self._busy.discard(qhash)
                if self._stop:
                    return  # the window is closing: nobody to tell
                if size != self._size:
                    continue  # the tile size changed meanwhile
                if img is None:
                    self._failed.add(qhash)
                else:
                    (self._soft.add if soft else self._soft.discard)(qhash)
                    old = self._cache.pop(qhash, None)
                    if old is not None:
                        self._bytes -= old.sizeInBytes()
                    self._cache[qhash] = img
                    self._bytes += img.sizeInBytes()
                    while self._bytes > self._budget and len(self._cache) > 1:
                        _, old = self._cache.popitem(last=False)
                        self._bytes -= old.sizeInBytes()
            try:
                self.ready.emit(qhash)
            except RuntimeError:  # the app quit between the check above and now
                return

    @staticmethod
    def _decode(qhash: str, path: str, size: int):
        """The thumbnail file, cropped square from its middle and shrunk with Lanczos: Qt's
        shrink-while-decoding is quicker but loses much of the detail (a 170 px tile kept about 70%).
        Returns (the tile, whether it came from an old thumbnail)."""
        file, soft = thumb_file(qhash), False
        if not file.exists():
            file, soft = old_thumb_file(qhash), True
        if not file.exists():
            file, soft = photo_thumb(qhash, path), False  # raises if the photo can't be read (e.g. its drive is out)
        with Image.open(file) as im:
            im = im.convert("RGB")
        w, h = im.size
        if w <= 0 or h <= 0:
            return None, False
        s = min(w, h)
        left, top = (w - s) / 2, (h - s) / 2
        im = im.resize((size, size), Image.LANCZOS, box=(left, top, left + s, top + s))
        return to_qimage(im).convertToFormat(QImage.Format.Format_RGB32), soft
