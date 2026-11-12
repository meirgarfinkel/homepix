"""Pairing a phone with the HomePix app: its photos go into an import automation of its own, made once it
has paired (or one no other phone uses), and the phone scans the QR code with the app. The code works
once, for a few minutes (a new one is shown when it runs out), and only while this window is open."""

import contextlib
import html
import io
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout

from ...core import autoimport, phones


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
    "<tr><td valign='top'><b>1</b></td><td>Open the <b>HomePix</b> app on your phone.</td></tr>"
    "<tr><td valign='top'><b>2</b></td><td>Tap <b>Pair with a computer</b> and scan this code.</td></tr>"
    "<tr><td valign='top'><b>3</b></td><td>New photos are then sent here whenever the phone is on the same "
    "network as this computer.</td></tr>"
    "</table>"
)


class PairDialog(QDialog):
    def __init__(self, link, rules: list[autoimport.Rule], folders: list[str], parent=None):
        """rules: all the automations (those another phone uses aren't offered); folders: the library's."""
        super().__init__(parent)
        self.setWindowTitle("Pair a phone")
        self.setMinimumWidth(440)
        self.link = link
        self.phone_name = ""
        self.rules, self.folders = rules, folders
        self.made_rule = None  # the automation made for the phone, once it has paired
        self.excluded_now = False  # (its folder was inside the library, so it was excluded)
        self.pending = None  # the pairing open now
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        row = QHBoxLayout()
        row.addWidget(QLabel("Photos go to:"))
        self.rule = QComboBox()
        self.rule.addItem("Its own new import automation", None)
        for r in phones.free_rules(rules):
            self.rule.addItem(f"{r.name} (an existing automation)", r.id)
        self.rule.currentIndexChanged.connect(lambda _: self._new_code())
        row.addWidget(self.rule, 1)
        lay.addLayout(row)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.qr)
        self.steps = QLabel(STEPS)
        self.steps.setTextFormat(Qt.TextFormat.RichText)
        self.steps.setWordWrap(True)
        lay.addWidget(self.steps)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self.accept)
        lay.addWidget(self.buttons)
        self.timer = QTimer(self, interval=5000)
        self.timer.timeout.connect(self._tick)
        link.paired.connect(self._paired)
        self._new_code()
        self.timer.start()

    def _new_code(self):
        p = self.pending = phones.open_pairing(self.rule.currentData())
        if not self.link.refresh():
            self.qr.clear()
            self.status.setText(self.link.problem)
            return
        if not phones.addresses():
            self.qr.clear()
            self.status.setText("This computer isn't on a network right now. Connect it, then try again.")
            return
        pm = qr_pixmap(phones.qr_text(p, self.link.port), scale=5)
        self.qr.setPixmap(pm)
        self.qr.setMinimumSize(pm.size())
        self.status.setText("Waiting for your phone...")

    def _tick(self):
        p = phones.pairing()
        if p is not None and p.phone is None and time.time() > p.expires - 5:
            self._new_code()  # (run out: a fresh one)

    def _paired(self, name: str):
        self.phone_name = name
        p = self.pending
        if p is not None and p.new_rule:
            self.made_rule, self.excluded_now = phones.make_rule(name, p.rule_id, self.folders, self.rules)
            autoimport.save_rules([*autoimport.load_rules(), self.made_rule])
        self.timer.stop()
        phones.close_pairing()
        self.qr.hide()
        self.steps.hide()
        self.rule.setEnabled(False)
        self.status.setText(f"<b>{html.escape(name)}</b> is paired. Its new photos will come in here.")
        self.buttons.setStandardButtons(QDialogButtonBox.StandardButton.Ok)
        self.adjustSize()

    def done(self, r):
        self.timer.stop()
        with contextlib.suppress(RuntimeError, TypeError):
            self.link.paired.disconnect(self._paired)
        phones.close_pairing()
        self.link.refresh()  # (no phone paired after all: nothing to answer)
        super().done(r)
