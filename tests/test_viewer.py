"""The viewer: flicking through, zoom, the wheel, full screen, and a video's controls."""

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import wait_until  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, Signal  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from homepix.core.timeline import Item  # noqa: E402
from homepix.ui.views import viewer as viewer_mod  # noqa: E402
from homepix.ui.views.viewer import Viewer  # noqa: E402
from homepix.ui.views.widgets import JumpSlider, toggle_full_screen  # noqa: E402


def big_photo(path, size=(1200, 900)):
    Image.new("RGB", size, (90, 120, 150)).save(path)
    return path


def shown(app, items, index=0, size=(400, 300)):
    v = Viewer()
    v.resize(*size)
    v.show()
    v.show_items(items, index)
    wait_until(app, lambda: v._best_image() is not None)
    return v


def test_holding_an_arrow_key_decodes_only_where_it_stops(app, monkeypatch):
    from PySide6.QtGui import QImage

    made = []
    lock = threading.Lock()

    def slow_decode(path, max_side, video=False):
        with lock:
            made.append(path)
        time.sleep(0.02)
        return QImage(4, 4, QImage.Format.Format_RGB32), 4

    monkeypatch.setattr(viewer_mod, "decode", slow_decode)
    items = [Item(i, f"/nowhere/{i}.jpg", "2024-01-01 00:00:00", f"q{i}") for i in range(100)]
    v = Viewer()
    v.resize(400, 300)
    v.show()
    v.show_items(items, 0)
    for i in range(1, 61):  # (an arrow key held for two seconds)
        v.go(i)
    wait_until(app, lambda: not v._pending)
    assert len(made) < 20  # (it was one for every photo passed, and the two beside it)
    assert v._best_image() is not None  # the one it stopped on is shown
    v.close()


def test_double_clicking_a_video_goes_full_screen_and_keeps_it_playing(app, monkeypatch):
    toggled = []
    monkeypatch.setattr(Viewer, "_is_video", lambda self: True)
    monkeypatch.setattr(Viewer, "toggle_play", lambda self: toggled.append(1))
    v = Viewer()
    v.items, v.index = [Item(1, "/nowhere/a.mp4", "2024-01-01 00:00:00", "q", "video")], 0
    v.resize(400, 300)
    v.show()
    at = QPointF(200, 150)

    def send(kind):
        e = QMouseEvent(
            kind,
            at,
            v.mapToGlobal(at),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        app.sendEvent(v, e)

    for kind in (
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.MouseButtonRelease,
    ):
        send(kind)  # (how a double-click arrives)
    assert v.isFullScreen() and len(toggled) % 2 == 0  # (it paused or played on the first press, and back)
    v.close()


def test_leaving_full_screen_goes_back_to_maximized(app):
    w = QWidget()
    w.showMaximized()
    app.processEvents()
    toggle_full_screen(w)
    app.processEvents()
    assert w.isFullScreen()
    toggle_full_screen(w)
    app.processEvents()
    assert w.isMaximized() and not w.isFullScreen()
    w.close()


class FakePlayback(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.playing, self.seeks = True, []
        self.player = type("P", (), {"duration": lambda s: 60_000, "position": lambda s: 0})()
        self.audio = type("A", (), {"isMuted": lambda s: False})()

    def toggle(self):
        pass

    def seek(self, ms):
        self.seeks.append(ms)

    def set_muted(self, on):
        pass


def test_clicking_the_time_bar_jumps_there(app):
    from homepix.ui.views.video_player import Controls

    pb = FakePlayback()
    c = Controls(pb)
    c.resize(600, 56)
    c.show()
    app.processEvents()
    assert isinstance(c.bar, JumpSlider)
    bar = c.bar
    QTest.mouseClick(
        bar,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(int(bar.width() * 0.75), bar.height() // 2),
    )
    assert pb.seeks and abs(pb.seeks[-1] - 45_000) < 6_000  # (about three quarters of a minute in)
    c.close()


def test_wheel_zoom_keeps_the_point_under_the_cursor(app, tmp_path):
    p = big_photo(tmp_path / "a.png")
    v = shown(app, [Item(1, str(p), "2024-01-01 00:00:00", "q")])
    around = QPointF(130, 100)

    def under(at):
        r = v._image_rect(v._best_image())
        return (at.x() - r.x()) / r.width(), (at.y() - r.y()) / r.height()

    v.set_zoom(v._current_zoom() * 2, QPointF(200, 150))
    before = under(around)
    v.set_zoom(v._current_zoom() * 1.2, around)
    after = under(around)
    r = v._image_rect(v._best_image())
    assert abs(before[0] - after[0]) * r.width() < 1.5 and abs(before[1] - after[1]) * r.height() < 1.5
    v.close()


def test_the_wheel_steps_a_photo_per_notch_and_ignores_sideways(app, tmp_path):
    items = [Item(i, str(big_photo(tmp_path / f"{i}.png", (60, 40))), "2024-01-01 00:00:00", f"q{i}") for i in range(5)]
    v = shown(app, items, 2)

    def wheel(dx, dy):
        e = QWheelEvent(
            QPointF(50, 50),
            QPointF(v.mapToGlobal(QPoint(50, 50))),
            QPoint(),
            QPoint(dx, dy),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        v.wheelEvent(e)

    wheel(120, 0)  # sideways
    assert v.index == 2
    for _ in range(7):  # a touchpad's small steps
        wheel(0, -15)
    assert v.index == 2
    wheel(0, -15)  # a whole notch now
    assert v.index == 3
    wheel(0, 120)
    assert v.index == 2
    v.close()
