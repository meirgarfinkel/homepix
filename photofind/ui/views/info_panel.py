"""The info panel: date and where it came from, camera, size, dimensions, GPS, people, path."""

import datetime as dt
import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from ...core import open_db
from ...core.info import photo_info
from .. import workers
from .timeline import clock


def _lookup(photo_id):
    db = open_db()
    try:
        return photo_info(db, photo_id)
    finally:
        db.close()


def nice_date(taken: str) -> str:
    try:
        d = dt.datetime.fromisoformat(taken)
    except (TypeError, ValueError):
        return taken or "Unknown"
    return f"{d:%A, %B} {d.day}, {d.year} at {d:%H:%M}"


class InfoPanel(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setMinimumWidth(260)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(14, 12, 14, 12)
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
        self.show_photo(None)

    def show_photo(self, photo_id, count: int = 1):
        """photo_id None: nothing selected. count > 1: several selected (the info is for photo_id)."""
        self._id = photo_id
        if photo_id is None:
            self.label.setText("<p style='color:gray'>Select a photo to see its details.</p>")
            return
        self._count = count
        self._latest.run(_lookup, self._show, photo_id)

    def refresh(self):
        if self._id is not None:
            self.show_photo(self._id, self._count)

    def _show(self, info, err):
        if err is not None or info is None:
            self.label.setText(f"<p style='color:gray'>No details: {html.escape(str(err or 'not in the index'))}</p>")
            return
        e = html.escape
        rows = []

        def row(name, value, note=None):
            if value:
                extra = f"<br><span style='color:gray'>{e(note)}</span>" if note else ""
                rows.append(f"<p><b>{e(name)}</b><br>{value}{extra}</p>")

        head = f"<h3 style='margin-bottom:2px'>{e(info['name'])}</h3>"
        if self._count > 1:
            head += f"<p style='color:gray'>{self._count:,} photos selected; this is the last one clicked.</p>"
        if info.get("offline"):
            head += "<p style='color:#c0392b'>The file can't be reached right now (is its drive connected?).</p>"
        if info.get("error"):
            head += f"<p style='color:#c0392b'>This file couldn't be read: {e(info['error'])}</p>"
        video = info.get("kind") == "video"
        row("Recorded" if video else "Taken", e(nice_date(info["taken"])), info["date_source"])
        if info.get("duration"):
            row("Length", e(clock(info["duration"])))
        row("Camera", e(info.get("camera", "")), info.get("lens"))
        row("Exposure", e(info.get("exposure", "")))
        if "dimensions" in info:
            w, h = info["dimensions"]
            pixels = f"{w:,} × {h:,}" if video else f"{w:,} × {h:,} ({w * h / 1e6:.1f} megapixels)"
            row("Size", f"{pixels} · {e(info.get('size', ''))}")
        else:
            row("Size", e(info.get("size", "")))
        if "gps" in info:
            lat, lon = info["gps"]
            url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}"
            row("Location", f"{lat:.5f}, {lon:.5f} · <a href='{url}'>map</a>")
        row("People", e(", ".join(info["people"])))
        row("Folder", e(info["folder"]))
        self.label.setText(head + "".join(rows))
