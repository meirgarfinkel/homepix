"""The info panel: when it was taken and where that came from, camera, size, dimensions, GPS, people,
path."""

import datetime as dt
import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from ...core.db import open_db
from ...core.info import photo_info
from .. import theme, workers
from .timeline import clock


def _lookup(photo_id):
    db = open_db()
    try:
        return photo_info(db, photo_id)
    finally:
        db.close()


def nice_date(taken: str) -> str:
    """ "Friday, April 1, 2022 at 07:32:31"."""
    try:
        d = dt.datetime.fromisoformat(taken)
    except (TypeError, ValueError):
        return taken or "Unknown"
    return f"{d:%A, %B} {d.day}, {d.year} at {d:%H:%M:%S}"


class InfoPanel(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("info")
        self.setWidgetResizable(True)
        self.setMinimumWidth(280)
        body = QWidget()
        body.setObjectName("infoBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 20, 20, 16)
        self.head = QLabel()  # its name
        self.head.setWordWrap(True)
        self.head.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.head)
        lay.addSpacing(14)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.label.setOpenExternalLinks(True)
        lay.addWidget(self.label)
        lay.addStretch()
        self.setWidget(body)
        self._latest = workers.Latest()
        self._id = None
        self._info = None
        self.show_photo(None)
        theme.signals.changed.connect(self._recolour)

    def _recolour(self):
        if self._id is None:
            self.show_photo(None)
        elif self._info is not None:
            self._show(self._info, None)

    def show_photo(self, photo_id, count: int = 1):
        """photo_id None: nothing selected. count > 1: several selected (the info is for photo_id)."""
        self._id = photo_id
        if photo_id is None:
            self.head.hide()
            self.label.setText(f"<p style='color:{_c('muted')}'>Select a photo to see its details.</p>")
            return
        self._count = count
        self._latest.run(_lookup, self._show, photo_id)

    def refresh(self):
        if self._id is not None:
            self.show_photo(self._id, self._count)

    def _show(self, info, err):
        muted, danger = _c("muted"), _c("danger")
        if err is not None or info is None:
            self._info = None
            self.head.hide()
            self.label.setText(
                f"<p style='color:{muted}'>No details: {html.escape(str(err or 'not in the index'))}</p>"
            )
            return
        self._info = info
        e = html.escape
        rows = []

        def row(name, value, note=None):
            if value:
                extra = f"<br><span style='color:{muted}'>{e(note)}</span>" if note else ""
                rows.append(
                    f"<tr><td width='84' style='color:{muted}; padding: 0 8px 10px 0'>{e(name)}</td>"
                    f"<td style='padding-bottom: 10px'>{value}{extra}</td></tr>"
                )

        video = info.get("kind") == "video"
        self.head.setText(f"<p style='font-size:14px; font-weight:700; margin:0'>{e(info['name'])}</p>")
        self.head.show()
        head = ""
        if self._count > 1:
            head += (
                f"<p style='color:{muted}; margin:0 0 14px 0'>{self._count:,} photos selected; "
                "this is the last one clicked.</p>"
            )
        if info.get("offline"):
            head += f"<p style='color:{danger}'>The file can't be reached right now (is its drive connected?).</p>"
        if info.get("error"):
            head += f"<p style='color:{danger}'>This file couldn't be read: {e(info['error'])}</p>"
        if info.get("description"):
            head += f"<p>{e(info['description']).replace(chr(10), '<br>')}</p>"
        day, _, time = nice_date(info["taken"]).partition(" at ")
        row("Taken", e(day) + (f"<br>{e(time)}" if time else ""), info.get("date_source"))
        row("Keywords", e(", ".join(info.get("keywords", []))))
        if info.get("duration"):
            row("Length", e(clock(info["duration"])))
        row("Camera", e(info.get("camera", "")), info.get("lens"))
        row("Exposure", e(info.get("exposure", "")))
        if "dimensions" in info:
            w, h = info["dimensions"]
            pixels = f"{w:,} × {h:,}" if video else f"{w:,} × {h:,} ({w * h / 1e6:.1f} MP)"
            row("Size", f"{pixels}<br>{e(info.get('size', ''))}")
        else:
            row("Size", e(info.get("size", "")))
        if "gps" in info:
            lat, lon = info["gps"]
            url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}"
            row("Location", f"{lat:.5f}, {lon:.5f}<br><a href='{url}' style='color:{_c('accentText')}'>Open map</a>")
        row("People", e(", ".join(info["people"])))
        row("Folder", e(info["folder"]))
        self.label.setText(head + "<table cellspacing='0' cellpadding='0'>" + "".join(rows) + "</table>")


def _c(name: str) -> str:
    return theme.color(name).name()
