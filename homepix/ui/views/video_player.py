"""Playing videos in the viewer, with Qt's own player (FFmpeg inside: H.264, HEVC, MP4, MOV and more).

Each frame comes to the viewer as a picture, which it draws where a photo would go, so fitting to the
window and full screen work the same. The controls float over the bottom of the video: play/pause,
the time, a bar to jump around in, sound on/off, and opening it in another app. They fade away while
it plays and the mouse is still.
"""

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QSlider, QWidget

HIDE_AFTER_MS = 2500  # the controls fade this long after the mouse stops, while playing


def clock(ms: int) -> str:
    """'1:05', or '1:02:05' for an hour or more."""
    s = max(0, ms) // 1000
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Playback(QObject):
    """One video playing: its frames as pictures (frame), and where it's got to."""

    frame = Signal(QImage)
    changed = Signal()  # playing or paused, the position, the length, sound on or off
    failed = Signal(str)  # why it can't play

    muted = False  # (kept from one video to the next while HomePix runs)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(Playback.muted)
        self.player.setAudioOutput(self.audio)
        self.sink = QVideoSink(self)
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._frame)
        self.player.playbackStateChanged.connect(lambda _: self.changed.emit())
        self.player.positionChanged.connect(lambda _: self.changed.emit())
        self.player.durationChanged.connect(lambda _: self.changed.emit())
        self.player.errorOccurred.connect(self._error)
        self.player.mediaStatusChanged.connect(self._status)
        self.path = None
        self.image = None  # the last frame shown
        self._loaded = False  # frames from the video before can still come in until this one has loaded

    def play(self, path: str):
        """Start a video from the beginning."""
        if path != self.path:
            self.player.stop()
            self.path, self.image, self._loaded = path, None, False
            self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def stop(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.path, self.image, self._loaded = None, None, False

    @property
    def playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def toggle(self):
        if self.playing:
            self.player.pause()
        elif self.path is not None:
            self.player.play()

    def seek(self, ms: int):
        self.player.setPosition(max(0, min(self.player.duration(), ms)))

    def skip(self, seconds: int):
        self.seek(self.player.position() + seconds * 1000)

    def set_muted(self, muted: bool):
        Playback.muted = muted
        self.audio.setMuted(muted)
        self.changed.emit()

    def _frame(self, frame: QVideoFrame):
        if not frame.isValid() or self.path is None or not self._loaded:
            return
        image = frame.toImage()  # (turned the way the phone held it)
        if not image.isNull():
            self.image = image
            self.frame.emit(image)

    def _status(self, status):
        S = QMediaPlayer.MediaStatus
        if status in (S.LoadedMedia, S.BufferingMedia, S.BufferedMedia):
            self._loaded = True
        if status == S.EndOfMedia:
            self.changed.emit()  # (back to the play button, on the last frame)

    def _error(self, _error, text: str):
        if self.path is not None:
            self.failed.emit(text or "This video can't be played here.")


class _Button(QAbstractButton):
    """A round button with a drawn symbol: play, pause, sound, muted or open."""

    def __init__(self, kind: str, tip: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(36, 36)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # (keys stay with the viewer)

    def sizeHint(self):
        return QSize(36, 36)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawEllipse(QRectF(self.rect()))
        c = QRectF(self.rect()).center()
        white = QColor(255, 255, 255, 240)
        p.setBrush(white)
        p.setPen(Qt.PenStyle.NoPen)
        if self.kind == "play":
            p.drawPolygon(QPolygonF([c + QPointF(-5, -8), c + QPointF(-5, 8), c + QPointF(8, 0)]))
        elif self.kind == "pause":
            p.drawRoundedRect(QRectF(c.x() - 7, c.y() - 8, 5, 16), 1.5, 1.5)
            p.drawRoundedRect(QRectF(c.x() + 2, c.y() - 8, 5, 16), 1.5, 1.5)
        elif self.kind in ("sound", "muted"):
            p.drawPolygon(
                QPolygonF(
                    [
                        c + QPointF(-9, -3),
                        c + QPointF(-5, -3),
                        c + QPointF(0, -8),
                        c + QPointF(0, 8),
                        c + QPointF(-5, 3),
                        c + QPointF(-9, 3),
                    ]
                )
            )
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(white, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            if self.kind == "sound":
                for r in (4, 8):
                    path = QPainterPath()
                    path.arcMoveTo(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), -45)
                    path.arcTo(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), -45, 90)
                    p.drawPath(path)
            else:
                p.drawLine(c + QPointF(3, -4), c + QPointF(10, 4))
                p.drawLine(c + QPointF(3, 4), c + QPointF(10, -4))
        elif self.kind == "open":  # a box with an arrow out of it
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(white, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            box = QPainterPath()
            box.moveTo(c + QPointF(-1, -7))
            box.lineTo(c + QPointF(-7, -7))
            box.lineTo(c + QPointF(-7, 7))
            box.lineTo(c + QPointF(7, 7))
            box.lineTo(c + QPointF(7, 1))
            p.drawPath(box)
            p.drawLine(c + QPointF(0, 0), c + QPointF(8, -8))
            p.drawLine(c + QPointF(3, -8), c + QPointF(8, -8))
            p.drawLine(c + QPointF(8, -8), c + QPointF(8, -3))
        p.end()


class Controls(QWidget):
    """The bar over the bottom of a video."""

    open_elsewhere = Signal()

    def __init__(self, playback: Playback, parent=None):
        super().__init__(parent)
        self.playback = playback
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setFixedHeight(56)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 6, 16, 10)
        row.setSpacing(10)
        self.play_button = _Button("play", "Play (K)")
        self.play_button.clicked.connect(playback.toggle)
        self.time = QLabel("0:00")
        self.time.setStyleSheet("color: white; background: transparent;")
        self.bar = QSlider(Qt.Orientation.Horizontal)
        self.bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bar.sliderMoved.connect(playback.seek)
        self.bar.sliderPressed.connect(lambda: playback.seek(self.bar.value()))
        self.length = QLabel("0:00")
        self.length.setStyleSheet("color: rgba(255,255,255,0.75); background: transparent;")
        self.sound = _Button("sound", "Sound on or off (M)")
        self.sound.clicked.connect(lambda: playback.set_muted(not playback.audio.isMuted()))
        self.elsewhere = _Button("open", "Open in another app")
        self.elsewhere.clicked.connect(self.open_elsewhere)
        for w in (self.play_button, self.time):
            row.addWidget(w)
        row.addWidget(self.bar, 1)
        for w in (self.length, self.sound, self.elsewhere):
            row.addWidget(w)
        playback.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        pb = self.playback.player
        self.play_button.kind = "pause" if self.playback.playing else "play"
        self.play_button.setToolTip("Pause (K)" if self.playback.playing else "Play (K)")
        self.sound.kind = "muted" if self.playback.audio.isMuted() else "sound"
        if not self.bar.isSliderDown():
            self.bar.setRange(0, max(0, pb.duration()))
            self.bar.setValue(pb.position())
        self.time.setText(clock(pb.position()))
        self.length.setText(clock(pb.duration()))
        self.update()
        for b in (self.play_button, self.sound):
            b.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(8, 0, -8, -4), 14, 14)
        p.end()


class Fader(QObject):
    """Hides the controls a little after the mouse stops while the video plays."""

    def __init__(self, controls: Controls, playback: Playback):
        super().__init__(controls)
        self.controls, self.playback = controls, playback
        self.timer = QTimer(self, singleShot=True, interval=HIDE_AFTER_MS)
        self.timer.timeout.connect(self._hide)
        playback.changed.connect(self._maybe)

    def poke(self):
        """The mouse moved: show them, and start counting again."""
        if self.playback.path is None:
            return
        self.controls.show()
        self.controls.raise_()
        self.timer.start()

    def _maybe(self):
        if not self.playback.playing:
            self.timer.stop()
            if self.playback.path is not None:
                self.controls.show()
        elif not self.timer.isActive() and self.controls.isVisible():
            self.timer.start()

    def _hide(self):
        if self.playback.playing and not self.controls.underMouse():
            self.controls.hide()
        elif self.playback.playing:
            self.timer.start()
