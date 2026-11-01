"""Updates. At startup, a newer HomePix on GitHub is downloaded quietly and a bar offers to restart
into it. Install updates (the File menu) does the same on request, or says this is the latest version.
Each kind of copy is updated the way it was installed."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

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
        self.ready = None  # (kind, version, file) downloaded at startup, waiting for a restart
        window.update_bar.restart.connect(self.install_ready)

    def check_quietly(self):
        """At startup: download a newer HomePix without asking, then show the bar. Quiet on failure."""
        kind = updates.how_installed()
        if kind is None or self.busy:
            return
        self.busy = True
        workers.run(updates.latest_release, lambda rel, err: self._checked_quietly(kind, rel, err))

    def _checked_quietly(self, kind, release, err):
        asset = None if err is not None else updates.file_for(release, kind)
        if asset is None or not updates.newer(release):
            self.busy = False
            return
        version = release["tag_name"].lstrip("vV")
        dest = _fresh_folder() / asset["name"]

        def done(finished, err):
            self.busy = False
            if finished and err is None:
                self.ready = (kind, version, dest)
                self.w.update_bar.offer(version)

        workers.run_apart(updates.download, done, asset, dest)

    def install_ready(self):
        if self.ready and not self.busy:
            self.w.update_bar.hide()
            self.busy = True
            self._install(*self.ready, ask=False)

    def check(self):
        if self.ready:
            self.install_ready()
            return
        if self.busy:
            self.w.statusBar().showMessage("Getting the update...", 4000)
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
        dest = _fresh_folder() / asset["name"]
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

        workers.run_apart(updates.download, done, asset, dest, relay.moved.emit, lambda: bool(stop))

    # --- installing ---------------------------------------------------------------------------------

    def _install(self, kind, version, path, ask=True):
        if kind == "windows":  # the installer closes this copy, updates quietly and opens the new one
            os.startfile(str(path), arguments=WINDOWS_QUIET)  # noqa: S606 (our own downloaded, checked installer)
            self.w.close()
            return
        if kind == "appimage":
            workers.run(_replace_appimage, lambda res, err: self._installed(version, err, ask=ask), str(path))
        elif kind == "mac":
            workers.run(_replace_mac_app, lambda res, err: self._installed(version, err, ask=ask), str(path))
        else:
            workers.run(_install_deb, lambda res, err: self._installed(version, err, res, path, ask), str(path))

    def _installed(self, version, err, how=None, path=None, ask=True):
        self.busy = False
        if how == "no pkexec":  # no password prompt here: the software installer does it
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            return
        if err is not None:
            QMessageBox.warning(self.w, "Updates", f"Couldn't install the update:\n{err}")
            return
        if not ask:  # they pressed Restart to update
            restart(self.w)
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


# the Windows installer, run from the app: no questions, a progress bar, HomePix opens again after
WINDOWS_QUIET = "/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /relaunch=1"


def _fresh_folder():
    folder = data_dir() / "updates"
    shutil.rmtree(folder, ignore_errors=True)  # an older download (or a broken one)
    folder.mkdir(exist_ok=True)
    return folder


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


def mac_bundle() -> Path:
    """The HomePix.app this copy runs from (its program is HomePix.app/Contents/MacOS/HomePix)."""
    return Path(sys.executable).resolve().parents[2]


def _replace_mac_app(dmg):
    """Copy HomePix.app out of the downloaded disk image, then swap it in for this one (a running app
    can be replaced; the new one starts on restart)."""
    here = mac_bundle()
    if here.suffix != ".app":
        raise OSError("this copy isn't inside HomePix.app.")
    if "/AppTranslocation/" in str(here) or str(here).startswith("/Volumes/"):
        raise OSError("drag HomePix into the Applications folder first, and open it from there.")
    mount = Path(dmg).parent / "mounted"
    new, old = here.with_name(here.name + ".new"), here.with_name(here.name + ".old")
    mount.mkdir(exist_ok=True)
    subprocess.run(
        ["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), dmg],
        check=True,
        capture_output=True,
    )
    try:
        shutil.rmtree(new, ignore_errors=True)
        r = subprocess.run(["ditto", str(mount / "HomePix.app"), str(new)], capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError(r.stderr.strip() or f"couldn't copy the new HomePix into {here.parent}.")
    finally:
        subprocess.run(["hdiutil", "detach", "-quiet", str(mount)], capture_output=True)
    shutil.rmtree(old, ignore_errors=True)
    os.rename(here, old)
    try:
        os.rename(new, here)
    except OSError:
        os.rename(old, here)  # put this one back
        raise
    shutil.rmtree(old, ignore_errors=True)
    os.remove(dmg)


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
    if sys.platform == "darwin":  # opened the Mac way, as the app (with its Dock icon)
        QProcess.startDetached("sh", ["-c", 'sleep 1; exec open -n "$0"', str(mac_bundle())])
    else:
        app = os.environ.get("APPIMAGE") or sys.executable
        QProcess.startDetached("sh", ["-c", 'sleep 1; exec "$0"', app])
    window.close()
