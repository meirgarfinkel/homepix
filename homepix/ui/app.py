"""Starting the desktop app.

Only one runs at a time: opening it again brings the open window forward (showing the photo it was
given, if any: "Open With > HomePix" in the file manager). Started from the
applications menu (no terminal), its output goes to app.log in the data folder.
`--self-test` checks the parts a build can lose (AI libraries, HEIC, Qt's image plugins, the
window) and exits 0 if they all work. `--background` is how the login entry starts it (when it's set to
keep running in the background): no window until it's opened, and nothing at all if it's already open.
"""

import atexit
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
from . import workers
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
    from ..core import background

    hidden = background.FLAG in argv
    if hidden and _ask_open_copy(_server_name(), None) != "none":
        return 0  # started at login, but it's already running
    files = [] if hidden else given_files([a for a in app.arguments()[1:] if a != background.FLAG])
    server = single_instance(files)
    if server is None:
        return 0  # it was already open: that window has been brought forward (and shown the files)
    add_menu_entry()
    background.refresh()  # (the login entry points at this copy, if HomePix moved)
    app.setQuitOnLastWindowClosed(False)  # closing the window may only hide it: the window says when to quit

    from .main_window import MainWindow

    win = MainWindow()
    server.newConnection.connect(lambda: _listen(server.nextPendingConnection(), win))
    win.closing.connect(server.close)  # a launch from now on opens a new window, not this closing one
    win.closing.connect(app.quit)
    if not hidden:
        win.show()
    if files:
        win.opener.open(files)
    code = app.exec()
    _exit(code)


def _exit(code):
    """Leave without waiting long for background work (indexing and the database are safe to stop at
    any point). A change to the user's files under way is waited for."""
    from ..core import exiftool

    workers.wait_idle(10)  # (first: a change under way may be using ExifTool)
    exiftool.close_shared()  # else it can outlive the app
    atexit._run_exitfuncs()  # tidy-ups registered with atexit (os._exit skips them)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


# --- one at a time -----------------------------------------------------------------------------------


def _server_name() -> str:
    where = hashlib.sha1(str(data_dir()).encode()).hexdigest()[:10]  # one per data folder
    return f"homepix-{getpass.getuser()}-{where}"


def single_instance(files=()):
    """A local server other launches can find, or None if HomePix is already open (it's then
    told to show itself, and given the files to open). A copy that doesn't answer is closing (or
    stuck): it's waited for, and asked again, for up to 30 seconds."""
    name = _server_name()
    end = time.time() + 30
    while time.time() < end:
        said = _ask_open_copy(name, files)
        if said == "answered":
            return None
        if said == "none":
            break
    server = QLocalServer()
    if not server.listen(name):
        QLocalServer.removeServer(name)  # left over from one that crashed (or from one stuck)
        server.listen(name)
    return server


def _ask_open_copy(name, files) -> str:
    """Ask an open copy to show itself (and open the files): "answered", "silent" or "none" (no copy open).
    files None: only see whether one is open ("open" or "none")."""
    probe = QLocalSocket()
    probe.connectToServer(name)
    if not probe.waitForConnected(500):
        return "none"
    if files is None:
        probe.abort()
        return "open"
    if sys.platform == "win32":  # let the open window come to the front (else it only flashes)
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    # the paths, one per line, then a zero byte; the open window answers once it has them
    probe.write("\n".join(files).encode() + b"\0")
    probe.waitForBytesWritten(1000)
    answered = probe.waitForReadyRead(3000)
    probe.abort()
    return "answered" if answered else "silent"


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
            f'Exec="{appimage}" %F\nTryExec={appimage}\nIcon={icon}\nTerminal=false\n'  # (gone with the AppImage)
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


def _self_test_video(app, clip: Path):
    """A short video made here plays (Qt Multimedia and its FFmpeg are in the build)."""
    import cv2

    from .views.video_player import Playback

    out = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
    for _ in range(10):
        out.write(cv2.UMat(48, 64, cv2.CV_8UC3, (30, 120, 200)).get())
    out.release()
    playback = Playback()
    playback.set_muted(True)
    playback.play(str(clip))
    end = time.time() + 15
    while time.time() < end and playback.image is None:
        app.processEvents()
        time.sleep(0.02)
    if playback.image is None:
        raise RuntimeError("a video didn't play (Qt Multimedia or its FFmpeg is missing from this build)")
    playback.stop()


def _self_test_phones(tmp: Path):
    """Phones can connect: a key is made, an encrypted connection works, and the network announcement
    loads (a key of the test's own, on this computer only)."""
    import socket
    import ssl
    import threading

    import zeroconf  # noqa: F401

    from ..core import phones

    phones.addresses()
    cert, key = tmp / "test.crt", tmp / "test.key"
    phones._make_identity(cert, key)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert, key)
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname, client_ctx.verify_mode = False, ssl.CERT_NONE
    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]

        def answer():
            conn, _ = listener.accept()
            with server_ctx.wrap_socket(conn, server_side=True) as s:
                s.sendall(s.recv(5))

        t = threading.Thread(target=answer, daemon=True)
        t.start()
        with client_ctx.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=10)) as c:
            c.sendall(b"hello")
            if c.recv(5) != b"hello":
                raise RuntimeError("an encrypted connection didn't work")
        t.join(10)


def run_self_test(app) -> int:
    def ok(what):
        print(f"SELF-TEST: {what}", flush=True)

    try:
        import tempfile

        import cv2  # noqa: F401
        import onnxruntime  # noqa: F401
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
            _self_test_video(app, Path(tmp) / "test.mp4")
            ok("videos play.")
            _self_test_phones(Path(tmp))
        ok("phones can connect.")
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
