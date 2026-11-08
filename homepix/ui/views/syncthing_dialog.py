"""Setting up a phone with Syncthing, from an import automation: a QR code of this computer's
Syncthing ID to scan on the phone; Connect when the phone asks to; and the folder the phone then
shares is taken as Receive Only into an Import from folder."""

import io
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core import syncthing
from .. import workers

POLL_MS = 2000
DOWNLOADS = "https://syncthing.net/downloads/"


def qr_pixmap(text: str, scale: int = 6) -> QPixmap:
    """A QR code of text, black on white (what phone cameras read best), with its quiet border."""
    import segno

    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=scale, border=4, dark="#000000", light="#ffffff")
    pm = QPixmap()
    pm.loadFromData(buf.getvalue(), "PNG")
    return pm


def install_help() -> str:
    """How to get Syncthing running on this system."""
    if sys.platform == "win32":
        how = "Download Syncthing for Windows and start it."
    elif sys.platform == "darwin":
        how = "Download Syncthing for macOS and open it."
    else:
        how = (
            "Install it from your software centre, or with <code>sudo apt install syncthing</code>, then start it "
            "(and have it start by itself) with <code>systemctl --user enable --now syncthing</code>."
        )
    return f'{how} <a href="{DOWNLOADS}">syncthing.net/downloads</a>'


STEPS = (
    "<ol style='margin-left: -20px'>"
    "<li>On your phone, install <b>Syncthing-Fork</b> (Android) or <b>Möbius Sync</b> (iPhone).</li>"
    "<li>In the app, add a device and scan this code.</li>"
    "<li>Click <b>Connect</b> here when your phone shows up.</li>"
    "<li>On the phone, share your camera folder with this computer, set to <b>Send Only</b>.</li>"
    "</ol>"
)


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

        self.pairing = QWidget()
        row = QHBoxLayout(self.pairing)
        row.setContentsMargins(0, 0, 0, 0)
        code = QVBoxLayout()
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        code.addWidget(self.qr)
        self.device_id = QLabel()
        self.device_id.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.device_id.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.device_id.setWordWrap(True)
        self.device_id.setObjectName("hint")
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSizeF(self.font().pointSizeF() * 0.85)
        self.device_id.setFont(mono)
        code.addWidget(self.device_id)
        code.addStretch()
        row.addLayout(code)
        steps = QLabel(STEPS)
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(steps, 1)
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
        self.status.setText("Looking for Syncthing on this computer...")
        workers.run(lambda: (st := syncthing.connect(), st.my_id()), self._started)

    def _started(self, res, err):
        if self._closed:
            return
        if err is not None:
            msg = str(err) if isinstance(err, syncthing.NotRunning) else f"Couldn't reach Syncthing: {err}"
            self.status.setText(f"<b>{msg}</b><br>{install_help()}")
            self.again.show()
            return
        self.st, my_id = res
        self.qr.setPixmap(qr_pixmap(my_id))
        self.device_id.setText(my_id)
        self.status.setText("<b>Pair your phone with this computer.</b>")
        self.pairing.show()
        self.timer.start()
        self._poll()

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
