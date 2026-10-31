"""Starting the desktop app.

Only one runs at a time: opening it again brings the open window forward (showing the photo it was
given, if any: "Open With > HomePix" in the file manager). Started from the
applications menu (no terminal), its output goes to app.log in the data folder.
`--self-test` checks the parts a build can lose (AI libraries, HEIC, Qt's image plugins, the
window) and exits 0 if they all work.
"""

import getpass
import hashlib
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from .. import __version__
from ..core.config import data_dir
from ..core.media import HEIF  # noqa: F401  (registers the HEIC reader before anything decodes)
from .open_files import MIME_TYPES, given_files

ICON = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "homepix-icon.png"


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    self_test = "--self-test" in argv
    # from the applications menu there's no terminal (on Windows, no output at all): keep a log
    if sys.stdout is None or (not self_test and not sys.stdout.isatty()):
        log = open(data_dir() / "app.log", "a", buffering=1, encoding="utf-8")  # noqa: SIM115 (open while the app runs)
        sys.stdout = sys.stderr = log
        print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} HomePix {__version__}")

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName("HomePix")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("homepix")
    app.setDesktopFileName("homepix")
    if ICON.is_file():
        app.setWindowIcon(QIcon(str(ICON)))
    from . import theme

    theme.apply(app)

    if self_test:
        return run_self_test(app)
    files = given_files(app.arguments()[1:])
    server = single_instance(files)
    if server is None:
        return 0  # it was already open: that window has been brought forward (and shown the files)
    add_menu_entry()

    from .main_window import MainWindow

    win = MainWindow()
    server.newConnection.connect(lambda: _listen(server.nextPendingConnection(), win))
    win.show()
    if files:
        win.opener.open(files)
    return app.exec()


# --- one at a time -----------------------------------------------------------------------------------


def _server_name() -> str:
    where = hashlib.sha1(str(data_dir()).encode()).hexdigest()[:10]  # one per data folder
    return f"homepix-{getpass.getuser()}-{where}"


def single_instance(files=()):
    """A local server other launches can find, or None if HomePix is already open (it's then
    told to show itself, and given the files to open)."""
    name = _server_name()
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(500):
        if sys.platform == "win32":  # let the open window come to the front (else it only flashes)
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        # the paths, one per line, then a zero byte; the open window answers once it has them
        probe.write("\n".join(files).encode() + b"\0")
        probe.waitForBytesWritten(1000)
        probe.waitForReadyRead(3000)
        probe.disconnectFromServer()
        return None
    server = QLocalServer()
    if not server.listen(name):
        QLocalServer.removeServer(name)  # left over from one that crashed
        server.listen(name)
    return server


def _listen(sock, win):
    """Another launch: show the window, and open the files it was given."""
    got, handled = bytearray(), []

    def handle():
        if handled:
            return
        handled.append(True)
        paths = [p for p in got.decode(errors="replace").split("\n") if p]
        if paths:
            win.opener.open(paths)
        else:
            win.bring_forward()

    def read():
        got.extend(bytes(sock.readAll()))
        if b"\0" in got and not handled:
            del got[got.index(b"\0") :]
            sock.write(b"ok")  # it can go now
            sock.flush()
            handle()

    def gone():
        read()
        handle()
        sock.deleteLater()

    sock.readyRead.connect(read)
    sock.disconnected.connect(gone)
    if sock.bytesAvailable():
        read()
    if sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:  # it hung up already
        gone()


def add_menu_entry():
    """Running as an AppImage: put HomePix in the applications menu (once, or after it moved), saying
    which files it opens, so the file manager offers it under "Open With"."""
    appimage = os.environ.get("APPIMAGE")
    if not appimage or not ICON.is_file():
        return
    try:
        share = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        icon = data_dir() / "homepix-icon.png"
        icon.write_bytes(ICON.read_bytes())
        entry = share / "applications" / "homepix.desktop"
        text = (
            "[Desktop Entry]\nType=Application\nName=HomePix\n"
            "Comment=Browse, search and organize your photos\n"
            f'Exec="{appimage}" %F\nIcon={icon}\nTerminal=false\n'
            "Categories=Graphics;Photography;Viewer;\nKeywords=photos;pictures;search;faces;\n"
            f"MimeType={';'.join(MIME_TYPES)};\n"
        )
        if not entry.exists() or entry.read_text() != text:
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text(text)
            _refresh_menu(entry.parent)
    except OSError as e:
        print(f"Couldn't add a menu entry: {e!r}", flush=True)


def _refresh_menu(folder):
    """Tell the desktop the menu entry changed (it reads which apps open what from a cache)."""
    import contextlib
    import shutil
    import subprocess

    tool = shutil.which("update-desktop-database")
    if tool:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run([tool, str(folder)], timeout=10, capture_output=True)


# --- self-test ---------------------------------------------------------------------------------------


def run_self_test(app) -> int:
    def ok(what):
        print(f"SELF-TEST: {what}", flush=True)

    try:
        import tempfile

        import cv2  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
        from PIL import Image

        from ..core.ai import ClipImage, ClipText, Faces, model_path
        from ..core.thumbs import photo_thumb
        from .thumbs import ThumbLoader

        ok("AI libraries load.")
        from . import theme

        if app.font().family() != theme.FAMILY:
            raise RuntimeError(f"the app's font is missing from this build ({theme.FONT_DIR})")
        ok("the app's font loads.")
        if not HEIF:
            raise RuntimeError("HEIC support (pillow-heif) is missing from this build")
        ok("HEIC photos can be read.")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "test.jpg"
            Image.new("RGB", (640, 480), "teal").save(src)
            photo_thumb("selftest", str(src))
            tile, _ = ThumbLoader._decode("selftest", str(src), 64)
            if tile is None or tile.width() != 64:
                raise RuntimeError("couldn't make a tile from a JPEG thumbnail")
            ok("thumbnails decode.")
            from ..core import exiftool, metadata

            if getattr(sys, "frozen", False) and exiftool.bundled() is None:
                raise RuntimeError("ExifTool is missing from this build")
            metadata.write(src, {"XMP-dc:Description": "HomePix ✓", "XMP-dc:Subject": ["a", "b"]})
            d = metadata.read([src])[str(src)]
            if (d.description, d.keywords) != ("HomePix ✓", ["a", "b"]):
                raise RuntimeError(f"ExifTool didn't write what it should ({d.description!r}, {d.keywords})")
            exiftool.exiftool().close()
        ok("details can be written into photos (ExifTool).")
        white = Image.new("RGB", (640, 480), "white")
        if model_path("face_detect.onnx").exists() and model_path("face_embed.onnx").exists():
            Faces().find(white)
            ok("face recognition runs.")
        if all(model_path(n).exists() for n in ("clip_vision.onnx", "clip_text.onnx", "clip_tokenizer.json")):
            ClipImage().embed(white)
            ClipText().query("a dog on the beach")
            ok("photo search runs.")

        from .main_window import MainWindow

        win = MainWindow(index=False)
        win.show()
        end = time.time() + 20
        while time.time() < end and not win.loaded:
            app.processEvents()
            time.sleep(0.02)
        if not win.loaded:
            raise RuntimeError("the library didn't load")
        if win.grab().isNull():
            raise RuntimeError("the window couldn't be drawn")
        QTimer.singleShot(0, win.close)
        app.processEvents()
    except Exception as e:
        print(f"SELF-TEST FAILED: {e!r}", flush=True)
        return 1
    print("SELF-TEST OK: the window opened and the library loaded.", flush=True)
    return 0
