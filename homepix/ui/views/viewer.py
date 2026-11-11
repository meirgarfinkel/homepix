"""One photo, big: fit to the window or zoomed, with previous/next. A video plays right here (see
video_player.py), starting as soon as it's shown; its controls float over the bottom, and it can still
be opened in another app.

Photos are decoded with the core's reader (so HEIC and EXIF rotation work like everywhere else),
first at screen size; zooming in past that loads the full resolution. The next and previous photos
are decoded ahead, so flicking through is instant. N shows who's in it: a box on each face, with
their name; clicking one is how to name it or say it's someone else.
"""

from collections import OrderedDict
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ...core.media import load_photo
from ...core.video import video_frame, video_size
from .. import theme, workers
from ..thumbs import to_qimage

FULL = 1 << 15  # "no limit" for load_photo
MAX_ZOOM = 8.0


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


def _key(item):
    """What tells photos apart: the id, or the path for one outside the library."""
    return item.id if item.id is not None else item.path


class Viewer(QWidget):
    closed = Signal()
    current_changed = Signal(int)  # global item index
    face_clicked = Signal(dict, QPoint)  # a face (as from core.people.face_rows), where (global)
    faces_toggled = Signal(bool)

    no_player = False  # Qt's video player couldn't load here

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.index = -1
        self._shown_id = None
        self._images = OrderedDict()  # (path, max_side) -> (QImage, original's longest side), a few around
        self._decoded = {}  # path -> the fingerprint it had when decoded (it changes when the file does)
        self._pending = set()
        self._error = None
        self.zoom = None  # None: fit to window; else device pixels per image pixel
        self._center = QPointF(0.5, 0.5)  # the image point (0-1) at the window's center
        self._drag_from = None
        self.show_faces = False
        self.faces = []  # the faces in the photo shown, when show_faces
        self._faces_of = None  # whose: the photo's id
        # comparing the copies of a duplicate: (describe(index) -> (title, detail), keep(index),
        # other_group(index, +1 or -1)). Left and right go round the copies, and zoom stays at the same
        # size on screen from one to the next; up and down go to the group before or after.
        self.compare = None
        self._carry_width = None  # (comparing) the zoomed width to show the next copy at
        self._playback = None  # the video player (made the first time a video is shown)
        self._controls = None
        self._fader = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    # --- what to show --------------------------------------------------------------------------------

    def show_items(self, items, index: int):
        self.items = items
        now = {it.path: it.qhash for it in items}
        changed = {p for p, q in self._decoded.items() if now.get(p, q) != q}  # e.g. turned
        for key in [k for k in self._images if k[0] in changed]:
            del self._images[key]
        for p in changed:
            del self._decoded[p]
        same = 0 <= self.index < len(self.items) and 0 <= index < len(items) and _key(items[index]) == self._shown_id
        self.go(index, keep_view=same and not changed)

    def go(self, index: int, keep_view: bool = False):
        """keep_view: the same photo again (after a change): stay zoomed where it was."""
        if not self.items:
            return
        if self.compare is not None:
            index %= len(self.items)  # round and round the copies
            shown = self._best_image() if 0 <= self.index < len(self.items) else None
            if self.zoom is not None and shown is not None and index != self.index:
                self._carry_width = self._image_rect(shown).width()
                keep_view = True
        index = max(0, min(len(self.items) - 1, index))
        self.index = index
        self._shown_id = _key(self.items[index])
        if keep_view and self.zoom is not None:
            self._start_decode(index, FULL)
        if not keep_view:
            self.zoom = None
            self._center = QPointF(0.5, 0.5)
        self._error = None
        self._play_current()
        self._start_decode(index, self._screen_longest_side())
        for n in (index + 1, index - 1):  # read ahead
            if 0 <= n < len(self.items):
                self._start_decode(n, self._screen_longest_side())
        self.update()
        self.current_changed.emit(index)

    def drop_current(self) -> bool:
        """Take the photo shown out of what's gone through (it's going to the Trash) and show the next
        one at once. False if it was the last."""
        if not 0 <= self.index < len(self.items):
            return False
        self.items = self.items[: self.index] + self.items[self.index + 1 :]  # (a copy: the grid's list stays)
        if not self.items:
            self.index = -1
            return False
        self.go(min(self.index, len(self.items) - 1))
        return True

    def set_faces(self, photo_id, faces):
        """The faces in a photo (shown if it's still the one being looked at)."""
        self._faces_of, self.faces = photo_id, faces
        self.update()

    def toggle_faces(self):
        self.show_faces = not self.show_faces
        self.faces_toggled.emit(self.show_faces)
        self.update()

    def _current_faces(self):
        here = 0 <= self.index < len(self.items) and self.items[self.index].id == self._faces_of
        return self.faces if self.show_faces and here else []

    def _face_rect(self, f, rect: QRectF) -> QRectF:
        return QRectF(
            rect.x() + f["x"] * rect.width(),
            rect.y() + f["y"] * rect.height(),
            f["w"] * rect.width(),
            f["h"] * rect.height(),
        )

    def _face_at(self, pos):
        shown = self._best_image()
        if shown is None:
            return None
        rect = self._image_rect(shown)
        hits = [f for f in self._current_faces() if self._face_rect(f, rect).contains(pos)]
        return min(hits, key=lambda f: f["w"]) if hits else None  # the smallest: a face behind another

    def _draw_faces(self, p, rect: QRectF):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        metrics = QFontMetrics(p.font())
        for f in self._current_faces():
            box = self._face_rect(f, rect)
            named = f["name"] is not None
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 120), 4))
            p.drawRoundedRect(box, 6, 6)
            p.setPen(QPen(QColor(255, 255, 255, 235) if named else QColor(255, 210, 90, 235), 2))
            p.drawRoundedRect(box, 6, 6)
            label = f["name"] or "Who is this?"
            w, h = metrics.horizontalAdvance(label) + 12, metrics.height() + 4
            tag = QRectF(box.center().x() - w / 2, box.bottom() + 4, w, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(tag, 4, 4)
            p.setPen(QColor(255, 255, 255))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, label)

    def _screen_longest_side(self) -> int:
        screen = self.screen().size() * self.devicePixelRatioF()
        return int(max(screen.width(), screen.height()))

    def _start_decode(self, index: int, max_side: int):
        key = (self.items[index].path, max_side)
        qhash = self.items[index].qhash
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
            self._decoded[key[0]] = qhash
            while len(self._images) > 5:
                self._images.popitem(last=False)
            if self.index == index:
                self.update()

        workers.run(decode, done, *key, self.items[index].kind == "video", viewing=True)

    def _best_image(self):
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
        frame = self._frame()
        if frame is not None:  # a video playing (or paused)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(self._video_rect(frame), frame)
            if not self._playback.playing:
                self._draw_play(p)
            self._draw_compare(p)
            p.end()
            return
        shown = self._best_image()
        if shown is None:
            p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._error or "")
            if self._is_video() and self._error is None and not self._starting():
                self._draw_play(p)
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._carry_width is not None and self.zoom is not None:  # the same size as the copy before
            img, full_side = shown
            scale = self._carry_width / img.width()
            self.zoom = scale * self.devicePixelRatioF() * max(img.width(), img.height()) / full_side
        self._carry_width = None
        if self._is_video():  # its still, until the first frame comes
            rect = self._video_rect(shown[0])
            p.drawImage(rect, shown[0])
            if self._error:
                p.fillRect(self.rect(), QColor(0, 0, 0, 170))
                p.setPen(QColor(255, 255, 255))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._error)
            elif not self._starting():
                self._draw_play(p)
            self._draw_compare(p)
            p.end()
            return
        rect = self._image_rect(shown)
        p.drawImage(rect, shown[0])
        self._draw_faces(p, rect)
        self._draw_compare(p)
        p.end()

    def _draw_compare(self, p):
        """Comparing: which copy this is, whether it's kept, and the keys."""
        if self.compare is None or not 0 <= self.index < len(self.items):
            return
        title, detail = self.compare[0](self.index)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        big, small = theme.font(15, QFont.Weight.Bold), theme.font(12)
        fm_big, fm_small = QFontMetrics(big), QFontMetrics(small)
        w = max(fm_big.horizontalAdvance(title), fm_small.horizontalAdvance(detail)) + 48
        h = fm_big.height() + fm_small.height() + 22
        box = QRectF(20, 20, min(w, self.width() - 40), h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(box, 12, 12)
        kept = "✓" in title
        p.setPen(theme.color("accentText") if kept else QColor(255, 255, 255))
        p.setFont(big)
        p.drawText(box.adjusted(16, 10, -16, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, title)
        p.setPen(QColor(255, 255, 255, 200))
        p.setFont(small)
        p.drawText(
            box.adjusted(16, 12 + fm_big.height(), -16, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            fm_small.elidedText(detail, Qt.TextElideMode.ElideLeft, int(box.width()) - 28),
        )
        keys = "← →  switch copies   ·   ↑ ↓  other duplicates   ·   K  keep this one   ·   Esc  back"
        kw = fm_small.horizontalAdvance(keys) + 32
        bar = QRectF((self.width() - kw) / 2, self.height() - fm_small.height() - 40, kw, fm_small.height() + 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
        p.setPen(QColor(255, 255, 255, 220))
        p.drawText(bar, Qt.AlignmentFlag.AlignCenter, keys)

    def _starting(self) -> bool:
        """The video shown is on its way (its first frame hasn't come yet)."""
        pb = self._playback
        return pb is not None and self._is_video() and pb.path == self.items[self.index].path and pb.playing

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
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(c.x() - r * 0.28, c.y() - r * 0.42),
                    QPointF(c.x() - r * 0.28, c.y() + r * 0.42),
                    QPointF(c.x() + r * 0.45, c.y()),
                ]
            )
        )

    # --- videos ------------------------------------------------------------------------------------

    def _video(self):
        """The player, made the first time it's needed (Qt's media libraries load then). None where it
        can't load (a system without its sound library): videos then open in the system's player."""
        if self._playback is None:
            if Viewer.no_player:
                return None
            try:
                from .video_player import Controls, Fader, Playback
            except ImportError as e:
                print(f"Videos open in the system's player: {e}", flush=True)
                Viewer.no_player = True
                return None

            self._playback = Playback(self)
            self._playback.frame.connect(lambda _: self.update())
            self._playback.changed.connect(self.update)
            self._playback.failed.connect(self._cant_play)
            self._controls = Controls(self._playback, self)
            self._controls.open_elsewhere.connect(self.open_elsewhere)
            self._controls.hide()
            self._fader = Fader(self._controls, self._playback)
            self._place_controls()
        return self._playback

    def _play_current(self):
        """Start the video shown (from the beginning), or stop the one that was."""
        if self._is_video() and self.isVisible() and self._video() is not None:
            self._playback.play(self.items[self.index].path)
            self._fader.poke()
        elif self._playback is not None and self._playback.path is not None:
            self._playback.stop()
            self._controls.hide()

    def _cant_play(self, why: str):
        if self._is_video():
            self._error = f"Can't play this video here:\n{why}\n\nOpen it in another app with the button below."
            self._playback.image = None
            self._controls.show()
            self.update()

    def _frame(self):
        """The video's frame to draw now, or None (not started yet: its still is shown)."""
        pb = self._playback
        if pb is None or not self._is_video() or pb.path != self.items[self.index].path:
            return None
        return pb.image

    def toggle_play(self):
        if not self._is_video():
            return
        pb = self._video()
        if pb is None:
            return self.open_elsewhere()
        if pb.path != self.items[self.index].path:
            pb.play(self.items[self.index].path)
        else:
            pb.toggle()
        self._fader.poke()

    def play(self):
        """Enter (or P, K or Space) on a video: play it, or pause it."""
        self.toggle_play()

    def open_elsewhere(self):
        """The video in the system's own player."""
        if self._is_video():
            if self._playback is not None and self._playback.playing:
                self._playback.toggle()
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.items[self.index].path))

    def _place_controls(self):
        if self._controls is not None:
            w = min(self.width() - 32, 900)
            self._controls.setGeometry(int((self.width() - w) / 2), self.height() - 72, int(w), 56)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_controls()

    def showEvent(self, e):
        super().showEvent(e)
        if not e.spontaneous():  # (not just the window coming back from the taskbar)
            self._play_current()

    def hideEvent(self, e):
        super().hideEvent(e)
        if self._playback is not None and not e.spontaneous():  # (minimized: it plays on)
            self._playback.stop()
            self._controls.hide()

    def _video_rect(self, img) -> QRectF:
        """A video fills the window (sideways or up and down), keeping its shape."""
        s = min(self.width() / img.width(), self.height() / img.height())
        w, h = img.width() * s, img.height() * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _scale(self, shown) -> float:
        """Logical pixels per pixel of the decoded image."""
        img, full_side = shown
        ratio = full_side / max(img.width(), img.height())  # original pixels per decoded pixel
        dpr = self.devicePixelRatioF()
        if self.zoom is None:  # fit, but never blow a small photo up past its real size
            return min(self.width() / img.width(), self.height() / img.height(), ratio / dpr)
        return self.zoom * ratio / dpr

    def _image_rect(self, shown) -> QRectF:
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
        shown = self._best_image()
        if shown is None:
            return 1.0
        img, full_side = shown
        return self._scale(shown) * self.devicePixelRatioF() * max(img.width(), img.height()) / full_side

    def set_zoom(self, zoom, around: QPointF | None = None):
        shown = self._best_image()
        if shown is None or self._is_video():
            return
        if zoom is not None:
            if (self.items[self.index].path, FULL) not in self._images:
                self._start_decode(self.index, FULL)  # zooming in: get every pixel
            rect = self._image_rect(shown)
            if around is not None and rect.contains(around):
                self._center = QPointF((around.x() - rect.x()) / rect.width(), (around.y() - rect.y()) / rect.height())
            zoom = max(0.05, min(MAX_ZOOM, zoom))
        self.zoom = zoom
        self.update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier or self.zoom is not None:
            steps = e.angleDelta().y() / 120
            self.set_zoom(self._current_zoom() * (1.2**steps), e.position())
        else:  # wheel without Ctrl flicks through photos while fitted
            self.go(self.index + (1 if e.angleDelta().y() < 0 else -1))

    def mouseDoubleClickEvent(self, e):
        if self._face_at(e.position()) is not None:
            return
        if self._is_video():  # (as in other players: full screen, or back)
            w = self.window()
            return w.showNormal() if w.isFullScreen() else w.showFullScreen()
        self.set_zoom(None if self.zoom is not None else 1.0, e.position())

    def mousePressEvent(self, e):
        face = (
            self._face_at(e.position())
            if e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton)
            else None
        )
        if face is not None:
            self.face_clicked.emit(face, e.globalPosition().toPoint())
        elif e.button() == Qt.MouseButton.LeftButton and self._is_video():
            self.toggle_play()  # a click on the video plays or pauses it
        elif e.button() == Qt.MouseButton.LeftButton and self.zoom is not None:
            self._drag_from = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif e.button() == Qt.MouseButton.BackButton:
            self.closed.emit()

    def mouseMoveEvent(self, e):
        if self._fader is not None and self._is_video():
            self._fader.poke()
        shown = self._best_image()
        if self._drag_from is None or shown is None:
            if self._face_at(e.position()) is not None:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
            return
        rect = self._image_rect(shown)
        d = e.position() - self._drag_from
        self._drag_from = e.position()
        self._center = QPointF(
            min(1, max(0, self._center.x() - d.x() / rect.width())),
            min(1, max(0, self._center.y() - d.y() / rect.height())),
        )
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag_from = None
        self.unsetCursor()

    def keyPressEvent(self, e):
        do = self._keys().get(e.key())
        if do is not None:
            do()
        else:
            super().keyPressEvent(e)

    def _keys(self) -> dict:
        """What each key does now (a key missing here goes on to the window)."""
        K, keys = Qt.Key, {}
        for names, do in (
            ((K.Key_Right, K.Key_Space, K.Key_PageDown), lambda: self.go(self.index + 1)),
            ((K.Key_Left, K.Key_Backspace, K.Key_PageUp), lambda: self.go(self.index - 1)),
            ((K.Key_Home,), lambda: self.go(0)),
            ((K.Key_End,), lambda: self.go(len(self.items) - 1)),
            ((K.Key_Plus, K.Key_Equal), lambda: self.set_zoom(self._current_zoom() * 1.25)),
            ((K.Key_Minus,), lambda: self.set_zoom(self._current_zoom() / 1.25)),
            ((K.Key_0, K.Key_Z), lambda: self.set_zoom(None)),
            ((K.Key_1,), lambda: self.set_zoom(1.0)),
            (
                (K.Key_Escape,),
                lambda: self.window().showNormal() if self.window().isFullScreen() else self.closed.emit(),
            ),
            ((K.Key_N,), self.toggle_faces),
            (
                (K.Key_F, K.Key_F11),
                lambda: self.window().showNormal() if self.window().isFullScreen() else self.window().showFullScreen(),
            ),
        ):
            keys.update(dict.fromkeys(names, do))
        if self._is_video():  # play or pause, a jump back or ahead, sound on or off
            keys.update(dict.fromkeys((K.Key_Return, K.Key_Enter, K.Key_P, K.Key_K, K.Key_Space), self.toggle_play))
            keys[K.Key_J] = lambda: self._playback is not None and self._playback.skip(-10)
            keys[K.Key_L] = lambda: self._playback is not None and self._playback.skip(10)
            keys[K.Key_M] = lambda: self._playback is not None and self._playback.set_muted(
                not self._playback.audio.isMuted()
            )
        if self.compare is not None:  # a duplicate's copies: up and down go to the next group, K keeps this one
            keys[K.Key_Up] = lambda: self.compare[2](self.index, -1)
            keys[K.Key_Down] = lambda: self.compare[2](self.index, 1)
            keys[K.Key_K] = lambda: (self.compare[1](self.index), self.update())
        return keys
