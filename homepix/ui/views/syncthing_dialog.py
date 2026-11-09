"""Setting up a phone with Syncthing, from an import automation: Install or Start Syncthing on this
computer if it isn't running yet; a QR code of this computer's Syncthing ID to scan on the phone;
Connect when the phone asks to; and the folder the phone then shares is taken as Receive Only into
an Import from folder."""

import io
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core import syncthing
from .. import workers
from ..desktop import _host_env

POLL_MS = 2000
DOWNLOADS = "https://syncthing.net/downloads/"
WAIT_S = 30  # (how long a just-started Syncthing gets to answer)


def qr_pixmap(text: str, scale: int = 6) -> QPixmap:
    """A QR code of text, black on white (what phone cameras read best), with its quiet border."""
    import segno

    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=scale, border=4, dark="#000000", light="#ffffff")
    pm = QPixmap()
    pm.loadFromData(buf.getvalue(), "PNG")
    return pm


STEPS = (
    "<table cellspacing='0' cellpadding='5'>"
    "<tr><td valign='top'><b>1</b></td><td>On your phone, install <b>Syncthing-Fork</b> (Android) "
    "or <b>Möbius Sync</b> (iPhone).</td></tr>"
    "<tr><td valign='top'><b>2</b></td><td>In the app, add a device and scan this code.</td></tr>"
    "<tr><td valign='top'><b>3</b></td><td>Click <b>Connect</b> here when your phone shows up.</td></tr>"
    "<tr><td valign='top'><b>4</b></td><td>On the phone, share your camera folder with this computer, "
    "set to <b>Send Only</b>.</td></tr>"
    "</table>"
)


def _mono(widget) -> QFont:
    mono = QFont("monospace")
    mono.setStyleHint(QFont.StyleHint.Monospace)
    mono.setPointSizeF(widget.font().pointSizeF() * 0.9)
    return mono


class CopyRow(QFrame):
    """Text to copy (an ID, or commands for a terminal), with a Copy button beside it. boxed: on a
    shaded panel, like a terminal's."""

    def __init__(self, parent=None, boxed: bool = False):
        super().__init__(parent)
        row = QHBoxLayout(self)
        if boxed:
            self.setObjectName("copyBox")
            row.setContentsMargins(10, 6, 6, 6)
        else:
            row.setContentsMargins(0, 0, 0, 0)
        self.text = QLabel()
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text.setWordWrap(True)
        self.text.setFont(_mono(self))
        self.button = QPushButton("Copy")
        self.button.setAutoDefault(False)
        self.button.setToolTip("Copy to the clipboard")
        self.button.clicked.connect(self._copy)
        row.addWidget(self.text, 1)
        row.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_text(self, text: str):
        self.text.setText(text)

    def _copy(self):
        QGuiApplication.clipboard().setText(self.text.text())
        self.button.setText("Copied")
        QTimer.singleShot(1500, lambda: self.button.setText("Copy"))


def _install_then_wait(how):
    """Install or start Syncthing (how), then wait for it to answer: (Syncthing, its ID)."""
    why = how(_host_env())
    if why:
        raise RuntimeError(why)
    deadline = time.monotonic() + WAIT_S
    while True:
        try:
            st = syncthing.connect()
            return st, st.my_id()
        except syncthing.NotRunning:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


class SyncthingDialog(QDialog):
    def __init__(self, folder: str = "", parent=None):
        """folder: where the phone's photos should arrive (empty: a folder named after the phone in
        "Phone photos" in the home folder)."""
        super().__init__(parent)
        self.setWindowTitle("Set up with Syncthing")
        self.folder_hint = folder
        self.st = None
        self.phone = None  # (device id, name), once connected
        self.folder = ""  # where its photos arrive, once its folder is taken
        self.phone_name = ""
        self._busy = False
        self._closed = False  # (answers from Syncthing that come after closing are dropped)
        lay = QVBoxLayout(self)
        self.status = QLabel("Looking for Syncthing on this computer...")
        self.status.setWordWrap(True)
        self.status.setOpenExternalLinks(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.status)

        # not running yet: Install (or Start) it from here, or do the same by hand
        self.setup = QWidget()
        col = QVBoxLayout(self.setup)
        col.setContentsMargins(0, 4, 0, 0)
        col.setSpacing(8)
        buttons_row = QHBoxLayout()
        self.install_button = QPushButton()
        self.install_button.clicked.connect(self._install)
        self.download_button = QPushButton("Download page")
        self.download_button.setObjectName("linkButton")
        self.download_button.setAutoDefault(False)
        self.download_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DOWNLOADS)))
        buttons_row.addWidget(self.install_button)
        buttons_row.addSpacing(12)
        buttons_row.addWidget(self.download_button)
        buttons_row.addStretch()
        col.addLayout(buttons_row)
        self.working = QLabel()
        self.working.setObjectName("hint")
        self.working.setWordWrap(True)
        self.working.hide()
        col.addWidget(self.working)
        self.by_hand_label = QLabel("Or run this in a terminal:")
        self.by_hand_label.setObjectName("hint")
        col.addWidget(self.by_hand_label)
        self.by_hand = CopyRow(boxed=True)
        col.addWidget(self.by_hand)
        self.setup.hide()
        lay.addWidget(self.setup)

        self.pairing = QWidget()
        row = QHBoxLayout(self.pairing)
        row.setContentsMargins(0, 0, 0, 0)
        code = QVBoxLayout()
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        code.addWidget(self.qr)
        self.id_row = CopyRow()  # (for a phone that can't scan: paste it into the app instead)
        self.id_row.text.setObjectName("hint")
        self.device_id = self.id_row.text
        code.addWidget(self.id_row)
        code.addStretch()
        row.addLayout(code)
        steps = QLabel(STEPS)
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(steps, 1, Qt.AlignmentFlag.AlignTop)
        self.pairing.hide()
        lay.addWidget(self.pairing)

        self.found = QWidget()
        found_row = QHBoxLayout(self.found)
        found_row.setContentsMargins(0, 0, 0, 0)
        self.found_label = QLabel()
        self.found_label.setWordWrap(True)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("checkButton")
        self.connect_button.clicked.connect(self._connect_phone)
        found_row.addWidget(self.found_label, 1)
        found_row.addWidget(self.connect_button)
        self.found.hide()
        lay.addWidget(self.found)
        lay.addStretch()

        buttons = QDialogButtonBox()
        self.again = QPushButton("Try again")
        self.again.clicked.connect(self._start)
        self.again.hide()
        self.use = QPushButton("Use this folder")
        self.use.setEnabled(False)
        self.use.setDefault(True)
        buttons.addButton(self.again, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.use, QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        for b in (self.again, cancel):
            b.setAutoDefault(False)  # (Use this folder is the main one, once there's a folder)
        self.cancel = cancel
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._poll)
        self.resize(640, 420)
        self._start()

    # --- finding Syncthing --------------------------------------------------------------------------

    def _start(self):
        self.again.hide()
        self.setup.hide()
        self.status.setText("Looking for Syncthing on this computer...")
        workers.run(lambda: (st := syncthing.connect(), st.my_id()), self._started)

    def _started(self, res, err):
        if self._closed:
            return
        self.install_button.setEnabled(True)
        self.working.hide()
        if err is not None:
            self._show_setup(err)
            return
        self.setup.hide()
        self.install_button.setDefault(False)
        self.use.setDefault(True)
        self.st, my_id = res
        self.qr.setPixmap(qr_pixmap(my_id))
        self.device_id.setText(my_id)
        self.status.setText("<b>Pair your phone with this computer.</b>")
        self.pairing.show()
        self.timer.start()
        self._poll()

    def _show_setup(self, err):
        """Not running: what's wrong, and a button that sorts it out."""
        kind = getattr(err, "kind", "")
        msg = str(err) if isinstance(err, syncthing.NotRunning) else f"Couldn't reach Syncthing: {err}"
        self._installing = kind == syncthing.MISSING
        lines = syncthing.manual_commands().splitlines()
        if kind == syncthing.STOPPED:
            body = (
                "HomePix uses Syncthing to receive photos from your phone. "
                "Start it, and it starts by itself from now on."
            )
            self.install_button.setText("Start Syncthing")
            self.install_button.setVisible(True)
            commands = lines[-1] if len(lines) > 1 else ""  # (Windows: Start is the only way)
        elif kind == syncthing.MISSING:
            can = syncthing.install_command() is not None
            body = "HomePix uses Syncthing, a free app, to receive photos from your phone."
            body += (
                " Install it from here, or get it from its download page." if can else " Get it from its download page."
            )
            self.install_button.setText("Install Syncthing")
            self.install_button.setVisible(can)
            commands = "\n".join(lines)
        else:
            self.status.setText(f"<b>{msg}</b>")
            self.again.show()
            return
        self.status.setText(f"<b>{msg}</b><br>{body}")
        self.by_hand.set_text(commands)
        self.by_hand.setVisible(bool(commands))
        self.by_hand_label.setVisible(bool(commands))
        self.setup.show()
        self.again.show()
        if self.install_button.isVisible():
            self.use.setDefault(False)
            self.install_button.setDefault(True)  # (the main button while it's showing)
            self.install_button.setFocus()

    def _install(self):
        installing = self._installing
        self.install_button.setEnabled(False)
        self.again.hide()
        self.working.setText(
            "Installing Syncthing... Your computer may ask for your password. This can take a minute."
            if installing
            else "Starting Syncthing..."
        )
        self.working.show()
        workers.run_apart(_install_then_wait, self._installed, syncthing.install if installing else syncthing.start)

    def _installed(self, res, err):
        if self._closed:
            return
        if err is not None and not isinstance(err, syncthing.NotRunning):
            self.install_button.setEnabled(True)
            self.again.show()
            self.working.setText(f"That didn't work: {err}")
            return
        self._started(res, err)

    # --- waiting for the phone ------------------------------------------------------------------------

    def _poll(self):
        if self._busy or self.st is None or self.folder:
            return
        self._busy = True
        st, phone = self.st, self.phone

        def look():
            if phone is not None:
                return "folders", phone, st.pending_folders(phone[0])
            pending = st.pending_devices()
            if pending:
                return "devices", None, pending
            me = st.my_id()  # a phone paired before (set up once already): its folder will do
            for device_id, name in st.devices().items():
                if device_id != me and (offered := st.pending_folders(device_id)):
                    return "folders", (device_id, name), offered
            return "devices", None, {}

        workers.run(look, self._looked)

    def _looked(self, res, err):
        self._busy = False
        if self._closed or err is not None:
            return
        kind, phone, pending = res
        if kind == "devices":
            if not pending:
                self.found.hide()
                return
            device_id, name = next(iter(pending.items()))
            self._asking = (device_id, name)
            self.found_label.setText(
                f'<b>"{name}" wants to connect.</b> Is that your phone? Its ID starts with {device_id[:7]}.'
            )
            self.found.show()
            return
        if pending:
            self.phone = phone
            folder_id, label = next(iter(pending.items()))
            self._take_folder(folder_id, label)

    def _connect_phone(self):
        device_id, name = self._asking
        self.connect_button.setEnabled(False)

        def done(_, err):
            if self._closed:
                return
            self.connect_button.setEnabled(True)
            if err is not None:
                self.found_label.setText(f"Couldn't connect: {err}")
                return
            self.phone = (device_id, name)
            self.found.hide()
            self.status.setText(
                f'<b>Connected to "{name}".</b> Now, on the phone, share your camera folder with this computer, '
                "set to <b>Send Only</b>."
            )

        workers.run(self.st.add_device, done, device_id, name)

    def _take_folder(self, folder_id: str, label: str):
        device_id, name = self.phone
        path = self.folder_hint or str(Path.home() / "Phone photos" / name)
        self._busy = True

        def done(_, err):
            self._busy = False
            if self._closed:
                return
            if err is not None:
                self.status.setText(f"Couldn't take the shared folder: {err}")
                return
            self.timer.stop()
            self.folder, self.phone_name = path, name
            self.pairing.hide()
            self.status.setText(
                f'<b>All set.</b> Photos from "{label}" on "{name}" will arrive in<br><code>{path}</code><br><br>'
                "It's set to Receive Only, so emptying it after importing never deletes anything on the phone."
            )
            self.use.setEnabled(True)
            self.use.setDefault(True)
            self.use.setFocus()
            QTimer.singleShot(0, self._fit_height)

        workers.run(self.st.accept_folder, done, folder_id, label, path, device_id)

    def _fit_height(self):
        """As wide as before, as tall as what's left needs (the code is gone)."""
        need = self.layout().totalHeightForWidth(self.width())
        if need > 0:
            self.resize(self.width(), need)

    def done(self, r):
        self._closed = True
        self.timer.stop()
        super().done(r)
