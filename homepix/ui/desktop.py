"""Working with the rest of the desktop: the clipboard (files copied or cut here paste in GNOME Files,
Dolphin, Nemo, Thunar and Windows Explorer, and the other way round), opening a file in another app,
and showing it in the file manager."""

import os
import shlex
import shutil
import subprocess
import sys

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication

GNOME = "x-special/gnome-copied-files"  # GNOME Files, Nemo, Caja, Thunar: "copy" or "cut", then the URLs
KDE_CUT = "application/x-kde-cutselection"  # Dolphin: "1" when cut
WIN_EFFECT = 'application/x-qt-windows-mime;value="Preferred DropEffect"'  # Explorer: 2 when cut, 5 copied
_MOVE, _COPY = 2, 5


def files_mime(paths, cut: bool = False) -> QMimeData:
    mime = QMimeData()
    urls = [QUrl.fromLocalFile(os.path.abspath(p)) for p in paths]
    mime.setUrls(urls)
    mime.setData(GNOME, ("cut" if cut else "copy").encode() + b"".join(b"\n" + u.toEncoded() for u in urls))
    mime.setData(KDE_CUT, b"1" if cut else b"0")
    if sys.platform == "win32":
        mime.setData(WIN_EFFECT, (_MOVE if cut else _COPY).to_bytes(4, "little"))
    mime.setText("\n".join(os.path.abspath(p) for p in paths))
    return mime


def copy_files(paths, cut: bool = False):
    QGuiApplication.clipboard().setMimeData(files_mime(paths, cut))


def files_in(mime) -> tuple[list[str], bool]:
    """The local files on the clipboard, and whether they were cut (to be moved) rather than copied."""
    if mime is None:
        return [], False
    cut = False
    if mime.hasFormat(GNOME):
        first = bytes(mime.data(GNOME)).split(b"\n", 1)[0].strip()
        cut = first == b"cut"
    elif mime.hasFormat(KDE_CUT):
        cut = bytes(mime.data(KDE_CUT)).strip() == b"1"
    elif mime.hasFormat(WIN_EFFECT):
        effect = int.from_bytes(bytes(mime.data(WIN_EFFECT))[:4], "little")
        cut = bool(effect & _MOVE and not effect & 1)  # move, and not copy
    paths = [os.path.normpath(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]  # (Qt's use /)
    return [p for p in paths if p], cut


def clipboard_files() -> tuple[list[str], bool]:
    return files_in(QGuiApplication.clipboard().mimeData())


def _host_env() -> dict:
    """The environment for another app. In the bundled app, its own libraries must not leak into it."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False) or os.environ.get("APPIMAGE"):
        for var in ("LD_LIBRARY_PATH", "QT_PLUGIN_PATH", "PYTHONHOME", "PYTHONPATH"):
            orig = os.environ.get(f"{var}_ORIG")
            if orig:
                env[var] = orig
            else:
                env.pop(var, None)
    return env


def _launch(args) -> bool:
    try:
        subprocess.Popen(
            args, env=_host_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        return True
    except OSError:
        return False


def open_file(path: str) -> bool:
    """Open in the app the desktop uses for that kind of file."""
    if sys.platform.startswith("linux") and shutil.which("xdg-open"):
        return _launch(["xdg-open", path])
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def split_command(command: str) -> list[str]:
    """A command line as its words (on Windows its own way: backslashes are part of paths there)."""
    if sys.platform != "win32":
        return shlex.split(command)
    return [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a for a in shlex.split(command, posix=False)]


def open_with(command: str, path: str) -> bool:
    """Open in a chosen app, e.g. "gimp" or "darktable --some-option" (the file goes at the end, or
    where %f is)."""
    args = split_command(command)
    if not args:
        return open_file(path)
    args = [path if a == "%f" else a for a in args] if "%f" in args else args + [path]
    return _launch(args)


def show_in_folder(path: str) -> bool:
    """Open the file manager at the file's folder, with the file selected where it can do that."""
    if sys.platform.startswith("linux") and shutil.which("dbus-send"):
        url = QUrl.fromLocalFile(path).toString()
        try:
            r = subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--print-reply",
                    "--dest=org.freedesktop.FileManager1",
                    "/org/freedesktop/FileManager1",
                    "org.freedesktop.FileManager1.ShowItems",
                    f"array:string:{url}",
                    "string:",
                ],
                env=_host_env(),
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    if sys.platform == "win32":  # (Explorer reads its command line its own way: the path quoted after the comma)
        return _launch(f'explorer /select,"{os.path.normpath(path)}"')
    if sys.platform == "darwin":
        return _launch(["open", "-R", path])
    return open_file(os.path.dirname(path) if os.path.isfile(path) else path)
