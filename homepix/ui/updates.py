"""Install updates (the ⋯ menu): if GitHub has a newer HomePix, download it and install it the way
this copy was installed. Otherwise say this is the latest version."""

import os
import shutil
import subprocess
import sys

from PySide6.QtCore import QObject, QProcess, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from ..core import updates
from ..core.config import data_dir
from . import workers


class _Progress(QObject):
    moved = Signal(int, int)  # bytes done, total


class Updater:
    def __init__(self, window):
        self.w = window
        self.busy = False

    def check(self):
        if self.busy:
            return
        kind = updates.how_installed()
        if kind is None:
            QMessageBox.information(self.w, "Updates", "This copy runs from the source code. Update it with git pull.")
            return
        self.busy = True
        self.w.statusBar().showMessage("Checking for updates...")
        workers.run(updates.latest_release, lambda rel, err: self._checked(kind, rel, err))

    def _checked(self, kind, release, err):
        self.w.statusBar().clearMessage()
        if err is not None:
            self.busy = False
            QMessageBox.warning(self.w, "Updates", f"Couldn't check for updates:\n{err}")
            return
        asset = updates.file_for(release, kind)
        if not updates.newer(release) or asset is None:
            self.busy = False
            QMessageBox.information(self.w, "Updates", "This is the latest version.")
            return
        self._download(kind, release["tag_name"].lstrip("vV"), asset)

    def _download(self, kind, version, asset):
        folder = data_dir() / "updates"
        shutil.rmtree(folder, ignore_errors=True)  # an older download (or a broken one)
        folder.mkdir(exist_ok=True)
        dest = folder / asset["name"]
        dlg = QProgressDialog(f"Downloading HomePix {version}...", "Cancel", 0, 100, self.w)
        dlg.setWindowTitle("Updates")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        stop = []
        dlg.canceled.connect(lambda: stop.append(True))
        relay = _Progress(dlg)
        relay.moved.connect(lambda done, total: dlg.setValue(int(100 * done / total) if total else 0))

        def done(finished, err):
            dlg.close()
            dlg.deleteLater()
            if err is not None:
                self.busy = False
                QMessageBox.warning(self.w, "Updates", f"Couldn't download the update:\n{err}")
            elif finished:
                self._install(kind, version, dest)
            else:
                self.busy = False

        workers.run(updates.download, done, asset, dest, relay.moved.emit, lambda: bool(stop))

    # --- installing ---------------------------------------------------------------------------------

    def _install(self, kind, version, path):
        if kind == "windows":  # the installer replaces this copy, so it goes; the new one opens at the end
            os.startfile(str(path))  # noqa: S606 (our own downloaded, checked installer)
            self.w.close()
            return
        if kind == "appimage":
            workers.run(_replace_appimage, lambda res, err: self._installed(version, err), str(path))
        else:
            workers.run(_install_deb, lambda res, err: self._installed(version, err, res, path), str(path))

    def _installed(self, version, err, how=None, path=None):
        self.busy = False
        if how == "no pkexec":  # no password prompt here: the software installer does it
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            return
        if err is not None:
            QMessageBox.warning(self.w, "Updates", f"Couldn't install the update:\n{err}")
            return
        answer = QMessageBox.question(
            self.w,
            "Updates",
            f"HomePix {version} is installed. Restart now?",
            QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            restart(self.w)


def _replace_appimage(new):
    """Put the new AppImage where this one is (a running AppImage can be replaced)."""
    here = os.environ["APPIMAGE"]
    os.chmod(new, 0o755)
    try:
        os.replace(new, here)
    except OSError:  # on another drive: copy beside it, then swap
        shutil.copy2(new, here + ".new")
        os.replace(here + ".new", here)
        os.remove(new)


def _install_deb(path):
    """Install the .deb: Linux asks for the password, then apt installs it. 'no pkexec' if there's no
    way to ask for it here (the software installer is opened instead)."""
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return "no pkexec"
    r = subprocess.run([pkexec, "apt-get", "install", "-y", path], capture_output=True, text=True)
    if r.returncode in (126, 127):  # the password prompt was closed
        raise OSError("it was cancelled.")
    if r.returncode != 0:
        said = (r.stderr or r.stdout).strip().splitlines()
        raise OSError(said[-1] if said else "the package couldn't be installed.")
    os.remove(path)
    return "installed"


def restart(window):
    """Start the new copy once this one has gone (one HomePix runs at a time), then close."""
    app = os.environ.get("APPIMAGE") or sys.executable
    QProcess.startDetached("sh", ["-c", 'sleep 1; exec "$0"', app])
    window.close()
