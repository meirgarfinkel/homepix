"""One photo, big: fit to the window or zoomed, with previous/next. A video shows a frame with a play
button; playing it opens the system's video player.

Photos are decoded with the core's reader (so HEIC and EXIF rotation work like everywhere else),
first at screen size; zooming in past that loads the full resolution. The next and previous photos
are decoded ahead, so flicking through is instant.
"""

from collections import OrderedDict
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPalette, QPolygonF
from PySide6.QtWidgets import QWidget

from ...core import load_photo
from ...core.video import video_frame, video_size
from .. import workers

FULL = 1 << 15  # "no limit" for load_photo
MAX_ZOOM = 8.0


def to_qimage(img) -> QImage:
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    return QImage(data, img.width, img.height, 3 * img.width, QImage.Format.Format_RGB888).copy()


def decode(path: str, max_side: int, video: bool = False):
    """The photo (or a video's frame) at most max_side big, and the longest side of the original."""
    if video:
        frame = video_frame(path, max_side)
        if frame is None:
            raise ValueError("this video's picture can't be decoded, but it may still play")
        return to_qimage(frame), max(video_size(path) or frame.size)
    with Image.open(path) as original:
        full_side = max(original.size)
    return to_qimage(load_photo(Path(path), max_side)), full_side


class Viewer(QWidget):
    closed = Signal()
    current_changed = Signal(int)  # global item index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.index = -1
        self._images = OrderedDict()  # (path, max_side) -> (QImage, original's longest side), a few around
        self._pending = set()
        self._error = None
        self.zoom = None  # None: fit to window; else device pixels per image pixel
        self._center = QPointF(0.5, 0.5)  # the image point (0-1) at the window's center
        self._drag_from = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    # --- what to show --------------------------------------------------------------------------------

    def show_items(self, items, index: int):
        self.items = items
        self.go(index)

    def go(self, index: int):
        if not self.items:
            return
        index = max(0, min(len(self.items) - 1, index))
        self.index = index
        self.zoom = None
        self._center = QPointF(0.5, 0.5)
        self._error = None
        self._load(index, self._screen_side())
        for n in (index + 1, index - 1):  # read ahead
            if 0 <= n < len(self.items):
                self._load(n, self._screen_side())
        self.update()
        self.current_changed.emit(index)

    def _screen_side(self) -> int:
        screen = self.screen().size() * self.devicePixelRatioF()
        return int(max(screen.width(), screen.height()))

    def _load(self, index: int, max_side: int):
        key = (self.items[index].path, max_side)
        if key in self._images or key in self._pending:
            return
        self._pending.add(key)

        def done(res, err):
            self._pending.discard(key)
            if err is not None:
                if self.index == index:
                    self._error = f"Can't show this photo:\n{err}"
                    self.update()
                return
            self._images[key] = res
            while len(self._images) > 5:
                self._images.popitem(last=False)
            if self.index == index:
                self.update()

        workers.run(decode, done, *key, self.items[index].kind == "video")

    def _image(self):
        """The sharpest decoded version of the current photo there is: (QImage, original's longest
        side), or None while it's loading."""
        path = self.items[self.index].path
        best = None
        for (p, side), res in self._images.items():
            if p == path and (best is None or side > best[0]):
                best = (side, res)
        return best[1] if best else None

    # --- drawing -------------------------------------------------------------------------------------

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self.index < 0:
            return
        shown = self._image()
        if shown is None:
            p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._error or "")
            if self._is_video():
                self._draw_play(p)
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(self._target_rect(shown), shown[0])
        if self._is_video():
            self._draw_play(p)
        p.end()

    def _is_video(self) -> bool:
        return 0 <= self.index < len(self.items) and self.items[self.index].kind == "video"

    def _play_rect(self) -> QRectF:
        r = min(self.width(), self.height()) * 0.09 + 24
        return QRectF(self.width() / 2 - r, self.height() / 2 - r, 2 * r, 2 * r)

    def _draw_play(self, p):
        box = self._play_rect()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 140))
        p.drawEllipse(box)
        c, r = box.center(), box.width() / 2
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawPolygon(QPolygonF([QPointF(c.x() - r * 0.28, c.y() - r * 0.42), QPointF(c.x() - r * 0.28, c.y() + r * 0.42),
                                 QPointF(c.x() + r * 0.45, c.y())]))

    def play(self):
        """Open the video in the system's player (Photo Finder has none of its own)."""
        if self._is_video():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.items[self.index].path))

    def _scale(self, shown) -> float:
        """Logical pixels per pixel of the decoded image."""
        img, full_side = shown
        ratio = full_side / max(img.width(), img.height())  # original pixels per decoded pixel
        dpr = self.devicePixelRatioF()
        if self.zoom is None:  # fit, but never blow a small photo up past its real size
            return min(self.width() / img.width(), self.height() / img.height(), ratio / dpr)
        return self.zoom * ratio / dpr

    def _target_rect(self, shown) -> QRectF:
        s = self._scale(shown)
        w, h = shown[0].width() * s, shown[0].height() * s
        if self.zoom is None:
            return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        x = self.width() / 2 - self._center.x() * w
        y = self.height() / 2 - self._center.y() * h
        if w <= self.width():
            x = (self.width() - w) / 2
        if h <= self.height():
            y = (self.height() - h) / 2
        return QRectF(x, y, w, h)

    # --- zoom and pan --------------------------------------------------------------------------------

    def _current_zoom(self) -> float:
        """Device pixels per original pixel right now."""
        shown = self._image()
        if shown is None:
            return 1.0
        img, full_side = shown
        return self._scale(shown) * self.devicePixelRatioF() * max(img.width(), img.height()) / full_side

    def set_zoom(self, zoom, around: QPointF | None = None):
        shown = self._image()
        if shown is None or self._is_video():
            return
        if zoom is not None:
            if (self.items[self.index].path, FULL) not in self._images:
                self._load(self.index, FULL)  # zooming in: get every pixel
            rect = self._target_rect(shown)
            if around is not None and rect.contains(around):
                self._center = QPointF((around.x() - rect.x()) / rect.width(), (around.y() - rect.y()) / rect.height())
            zoom = max(0.05, min(MAX_ZOOM, zoom))
        self.zoom = zoom
        self.update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier or self.zoom is not None:
            steps = e.angleDelta().y() / 120
            self.set_zoom(self._current_zoom() * (1.2 ** steps), e.position())
        else:  # wheel without Ctrl flicks through photos while fitted
            self.go(self.index + (1 if e.angleDelta().y() < 0 else -1))

    def mouseDoubleClickEvent(self, e):
        if self._is_video():
            return self.play()
        self.set_zoom(None if self.zoom is not None else 1.0, e.position())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._is_video() and self._play_rect().contains(e.position()):
            self.play()
        elif e.button() == Qt.MouseButton.LeftButton and self.zoom is not None:
            self._drag_from = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif e.button() == Qt.MouseButton.BackButton:
            self.closed.emit()

    def mouseMoveEvent(self, e):
        shown = self._image()
        if self._drag_from is None or shown is None:
            return
        rect = self._target_rect(shown)
        d = e.position() - self._drag_from
        self._drag_from = e.position()
        self._center = QPointF(min(1, max(0, self._center.x() - d.x() / rect.width())),
                               min(1, max(0, self._center.y() - d.y() / rect.height())))
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag_from = None
        self.unsetCursor()

    def keyPressEvent(self, e):
        key = e.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Space, Qt.Key.Key_PageDown):
            self.go(self.index + 1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace, Qt.Key.Key_PageUp):
            self.go(self.index - 1)
        elif key == Qt.Key.Key_Home:
            self.go(0)
        elif key == Qt.Key.Key_End:
            self.go(len(self.items) - 1)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.set_zoom(self._current_zoom() * 1.25)
        elif key == Qt.Key.Key_Minus:
            self.set_zoom(self._current_zoom() / 1.25)
        elif key in (Qt.Key.Key_0, Qt.Key.Key_Z):
            self.set_zoom(None)
        elif key == Qt.Key.Key_1:
            self.set_zoom(1.0)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_P) and self._is_video():
            self.play()
        elif key == Qt.Key.Key_Escape:
            if self.window().isFullScreen():
                self.window().showNormal()
            else:
                self.closed.emit()
        elif key in (Qt.Key.Key_F, Qt.Key.Key_F11):
            w = self.window()
            w.showNormal() if w.isFullScreen() else w.showFullScreen()
        else:
            super().keyPressEvent(e)
