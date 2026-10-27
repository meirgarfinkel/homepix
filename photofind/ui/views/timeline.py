"""The timeline: every photo by date, in month sections, with a year scrubber instead of a scroll bar.

It draws only the rows on screen, so the library's size doesn't matter. The layout is a list of
rows (a month header, or a row of tiles), each with its y position; finding what's on screen is a
binary search.
"""

import bisect
import calendar
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QToolTip, QWidget

from ...core.timeline import Month

HEADER_H = 48
SPACING = 3
MARGIN = 12
MIN_TILE, MAX_TILE, DEFAULT_TILE = 90, 360, 170
PREFETCH_SCREENS = 1.5  # also load this many screens of thumbnails ahead and behind


def clock(seconds) -> str:
    """A video's length: 0:42, 3:07, 1:02:15."""
    if not seconds:
        return ""
    s = round(seconds)
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass(slots=True)
class Row:
    y: int
    h: int
    month: int  # index into months
    start: int = -1  # first item (global index) for a tile row; -1 for a header
    end: int = -1


class TimelineView(QAbstractScrollArea):
    activated = Signal(int)  # global item index: open it
    current_changed = Signal(int)  # global item index, or -1
    selection_changed = Signal()
    menu_requested = Signal(QPoint)  # right-click (or the menu key), at this global position

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.loader = loader
        loader.ready.connect(self._thumb_ready)
        self.months: list[Month] = []
        self.items = []  # flat list, in display order
        self.rows: list[Row] = []
        self._row_y = []
        self._month_row = []  # month index -> its header row index
        self._tile_rows, self._tile_starts = [], []  # tile rows' indexes, and their first items
        self.tile = DEFAULT_TILE
        self.cols = 1
        self._cell = DEFAULT_TILE
        self._height = 0
        self.current = -1
        self.selected = set()
        self._anchor = -1
        self.empty_text = ""
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setAutoFillBackground(False)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.verticalScrollBar().setSingleStep(40)
        self.scrubber = Scrubber(self)
        self.setViewportMargins(0, 0, Scrubber.WIDTH, 0)

    # --- data and layout -----------------------------------------------------------------------------

    def set_months(self, months: list[Month], fresh: bool = False):
        """Show these (as from core.timeline.group_by_month). Keeps the selection and the photo at the
        top of the screen in place, if they're still there; fresh: start at the top with nothing
        selected (showing something else, rather than an update of the same)."""
        keep = -1 if fresh else self._top_item()
        keep_qhash = self.items[keep].qhash if 0 <= keep < len(self.items) else None
        cur = self.items[self.current].id if 0 <= self.current < len(self.items) and not fresh else None
        sel = set() if fresh else {self.items[i].id for i in self.selected}
        self.months = months
        self.items = [it for m in months for it in m.items]
        index = {it.id: i for i, it in enumerate(self.items)}
        self.current = index.get(cur, -1)
        self.selected = {index[i] for i in sel if i in index}
        self._relayout()
        if fresh:
            self.verticalScrollBar().setValue(0)
        if keep_qhash:
            for i, it in enumerate(self.items):
                if it.qhash == keep_qhash:
                    self._scroll_to_item_top(i)
                    break
        self.selection_changed.emit()

    def set_scrubber(self, on: bool):
        """The year scrubber (for anything in date order), or a plain scroll bar (search results)."""
        self.scrubber.setVisible(on)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff if on
                                        else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setViewportMargins(0, 0, Scrubber.WIDTH if on else 0, 0)
        self.scrubber.place()
        self._relayout()  # the grid is wider or narrower now

    def set_tile_size(self, px: int):
        px = max(MIN_TILE, min(MAX_TILE, px))
        if px != self.tile:
            keep = self._top_item()
            self.tile = px
            self._relayout()
            self._scroll_to_item_top(keep)

    def _relayout(self):
        width = max(1, self.viewport().width() - 2 * MARGIN)
        self.cols = max(1, (width + SPACING) // (self.tile + SPACING))
        self._cell = (width - SPACING * (self.cols - 1)) // self.cols  # tiles stretch to fill the width
        self.loader.set_size(round(self._cell * self.devicePixelRatioF()))
        rows, y, start = [], MARGIN // 2, 0
        self._month_row = []
        for mi, m in enumerate(self.months):
            self._month_row.append(len(rows))
            rows.append(Row(y, HEADER_H, mi))
            y += HEADER_H
            n = len(m.items)
            for s in range(0, n, self.cols):
                rows.append(Row(y, self._cell, mi, start + s, start + min(n, s + self.cols)))
                y += self._cell + SPACING
            start += n
            y += SPACING * 4
        self.rows = rows
        self._row_y = [r.y for r in rows]
        self._tile_rows = [k for k, r in enumerate(rows) if r.start != -1]
        self._tile_starts = [rows[k].start for k in self._tile_rows]
        self._height = y + MARGIN
        bar = self.verticalScrollBar()
        bar.setRange(0, max(0, self._height - self.viewport().height()))
        bar.setPageStep(self.viewport().height())
        self.viewport().update()
        self.scrubber.update()

    def resizeEvent(self, e):
        keep = self._top_item()
        super().resizeEvent(e)
        self._relayout()
        self._scroll_to_item_top(keep)
        self.scrubber.place()

    # --- geometry helpers ----------------------------------------------------------------------------

    def _row_at(self, y: int) -> int:
        return max(0, bisect.bisect_right(self._row_y, y) - 1)

    def _row_of_item(self, i: int) -> int:
        return self._tile_rows[bisect.bisect_right(self._tile_starts, i) - 1]

    def item_rect(self, i: int) -> QRect:
        """In content coordinates (add the scroll offset back for the viewport)."""
        r = self.rows[self._row_of_item(i)]
        c = i - r.start
        return QRect(MARGIN + c * (self._cell + SPACING), r.y, self._cell, self._cell)

    def item_at(self, pos: QPoint) -> int:
        y = pos.y() + self.verticalScrollBar().value()
        if not self.rows:
            return -1
        r = self.rows[self._row_at(y)]
        if r.start == -1 or y > r.y + r.h:
            return -1
        x = pos.x() - MARGIN
        c = x // (self._cell + SPACING)
        if x < 0 or c >= self.cols or x - c * (self._cell + SPACING) > self._cell:
            return -1
        i = r.start + c
        return i if i < r.end else -1

    def _top_item(self) -> int:
        if not self.rows:
            return -1
        k = self._row_at(self.verticalScrollBar().value())
        while k < len(self.rows) and self.rows[k].start == -1:
            k += 1
        return self.rows[k].start if k < len(self.rows) else -1

    def _scroll_to_item_top(self, i: int):
        if 0 <= i < len(self.items):
            k = self._row_of_item(i)
            if k > 0 and self.rows[k - 1].start == -1:
                k -= 1  # show its month header too
            self.verticalScrollBar().setValue(self.rows[k].y - MARGIN // 2)

    def ensure_visible(self, i: int):
        rect = self.item_rect(i)
        bar = self.verticalScrollBar()
        top, h = bar.value(), self.viewport().height()
        if rect.top() - HEADER_H < top:
            bar.setValue(rect.top() - HEADER_H)
        elif rect.bottom() > top + h:
            bar.setValue(rect.bottom() - h + MARGIN)

    def visible_months(self) -> tuple[int, int]:
        if not self.rows:
            return -1, -1
        y = self.verticalScrollBar().value()
        return self.rows[self._row_at(y)].month, self.rows[self._row_at(y + self.viewport().height())].month

    # --- drawing -------------------------------------------------------------------------------------

    def paintEvent(self, e):
        p = QPainter(self.viewport())
        pal = self.palette()
        p.fillRect(self.viewport().rect(), pal.color(QPalette.ColorRole.Base))
        if not self.items:
            p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       self.empty_text)
            return
        top = self.verticalScrollBar().value()
        h = self.viewport().height()
        p.translate(0, -top)
        head_font = QFont(self.font())
        head_font.setPointSizeF(head_font.pointSizeF() * 1.25)
        head_font.setWeight(QFont.Weight.DemiBold)
        sub_font = QFont(self.font())
        small_font = QFont(self.font())
        small_font.setPointSizeF(small_font.pointSizeF() * 0.85)
        small_font.setWeight(QFont.Weight.DemiBold)
        placeholder = pal.color(QPalette.ColorRole.AlternateBase)
        highlight = pal.color(QPalette.ColorRole.Highlight)
        k = self._row_at(top)
        while k < len(self.rows) and self.rows[k].y < top + h:
            r = self.rows[k]
            if r.start == -1:
                m = self.months[r.month]
                p.setPen(pal.color(QPalette.ColorRole.Text))
                p.setFont(head_font)
                title = m.title or (f"{calendar.month_name[m.month]} {m.year}" if m.month
                                    else str(m.year or "Unknown date"))
                box = QRect(MARGIN, r.y, self.viewport().width() - 2 * MARGIN, r.h)
                p.drawText(box, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
                p.setFont(sub_font)
                p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
                p.drawText(box, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           f"{len(m.items):,} photo{'s' if len(m.items) != 1 else ''}")
            else:
                for i in range(r.start, r.end):
                    it = self.items[i]
                    rect = QRect(MARGIN + (i - r.start) * (self._cell + SPACING), r.y, self._cell, self._cell)
                    img = self.loader.get(it.qhash)
                    video = it.kind == "video"
                    if img is not None:
                        p.drawImage(rect, img)
                    else:
                        p.fillRect(rect, placeholder)
                        if self.loader.failed(it.qhash):
                            p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
                            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "▶" if video else "Can't show\nthis photo")
                    if video:
                        self._video_badge(p, rect, it.duration, small_font)
                    if it.rating and self._cell >= 72:
                        self._pill(p, rect, "★" * it.rating, small_font, left=True)
                    if i in self.selected:
                        p.fillRect(rect, QColor(highlight.red(), highlight.green(), highlight.blue(), 70))
                        p.setPen(QPen(highlight, 3))
                        p.drawRect(rect.adjusted(1, 1, -2, -2))
                    if i == self.current and self.hasFocus():
                        p.setPen(QPen(pal.color(QPalette.ColorRole.Text), 1, Qt.PenStyle.DashLine))
                        p.drawRect(rect.adjusted(0, 0, -1, -1))
            k += 1
        p.end()
        self._request_thumbs(top, h)

    @staticmethod
    def _video_badge(p, rect, duration, font):
        """▶ and the length, bottom right, white on a dark pill."""
        TimelineView._pill(p, rect, f"▶ {clock(duration)}".strip(), font)

    @staticmethod
    def _pill(p, rect, text, font, left=False):
        """text in white on a dark pill, in the tile's bottom right (or left) corner."""
        p.setFont(font)
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 10, fm.height() + 2
        x = rect.left() + 5 if left else rect.right() - w - 5
        pill = QRect(x, rect.bottom() - h - 5, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.drawRoundedRect(pill, h / 2, h / 2)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QColor(255, 255, 255))
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

    def _request_thumbs(self, top, h):
        """On-screen tiles first, then the screens just below and above."""
        def items_between(y0, y1):
            out = []
            k = self._row_at(max(0, y0))
            while k < len(self.rows) and self.rows[k].y < y1:
                r = self.rows[k]
                if r.start != -1:
                    out.extend(range(r.start, r.end))
                k += 1
            return out

        ahead = int(h * PREFETCH_SCREENS)
        order = dict.fromkeys(items_between(top, top + h) + items_between(top + h, top + h + ahead)
                              + items_between(top - ahead, top)[::-1])  # rows on the edges come up twice
        self.loader.want([(self.items[i].qhash, self.items[i].path) for i in order])

    def _thumb_ready(self, qhash):
        self.viewport().update()

    def _scrolled(self, _):
        self.viewport().update()
        self.scrubber.update()

    # --- input ---------------------------------------------------------------------------------------

    def set_current(self, i: int, mode: str = "only"):
        """mode: 'only' selects just i, 'toggle' (Ctrl), 'range' (Shift), 'keep' leaves the selection."""
        if not self.items:
            return
        i = max(0, min(len(self.items) - 1, i))
        if mode == "only":
            self.selected = {i}
            self._anchor = i
        elif mode == "toggle":
            self.selected ^= {i}
            self._anchor = i
        elif mode == "range":
            a = self._anchor if self._anchor >= 0 else i
            self.selected = set(range(min(a, i), max(a, i) + 1))
        self.current = i
        self.ensure_visible(i)
        self.viewport().update()
        self.current_changed.emit(i)
        self.selection_changed.emit()

    def select_all(self):
        self.selected = set(range(len(self.items)))
        self.viewport().update()
        self.selection_changed.emit()

    def mousePressEvent(self, e):
        self.setFocus()
        i = self.item_at(e.position().toPoint())
        if e.button() != Qt.MouseButton.LeftButton:
            return
        mods = e.modifiers()
        if i < 0:
            if not mods:
                self.selected = set()
                self.viewport().update()
                self.selection_changed.emit()
            return
        if mods & Qt.KeyboardModifier.ShiftModifier:
            self.set_current(i, "range")
        elif mods & Qt.KeyboardModifier.ControlModifier:
            self.set_current(i, "toggle")
        else:
            self.set_current(i, "only")

    def contextMenuEvent(self, e):
        """Right-click a photo that isn't selected: it alone is chosen. On a selected one: all of them."""
        if e.reason() == e.Reason.Mouse:
            i = self.item_at(self.viewport().mapFromGlobal(e.globalPos()))
            if i >= 0 and i not in self.selected:
                self.set_current(i, "only")
        elif self.current >= 0:  # the menu key: at the current photo
            r = self.item_rect(self.current)
            self.menu_requested.emit(self.viewport().mapToGlobal(r.center()))
            return
        self.menu_requested.emit(e.globalPos())

    def selected_items(self) -> list:
        """The selected photos in display order (or the current one if none are selected)."""
        if self.selected:
            return [self.items[i] for i in sorted(self.selected)]
        return [self.items[self.current]] if 0 <= self.current < len(self.items) else []

    def mouseDoubleClickEvent(self, e):
        i = self.item_at(e.position().toPoint())
        if i >= 0 and e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(i)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:  # Ctrl+wheel: bigger or smaller tiles
            steps = e.angleDelta().y() / 120
            self.set_tile_size(round(self.tile * (1.12 ** steps)))
        else:
            super().wheelEvent(e)

    def _vertical(self, i: int, rows_down: int) -> int:
        """The item rows_down tile rows below (negative: above) item i, in the same column if it can."""
        k = self._row_of_item(i)
        col = i - self.rows[k].start
        step = 1 if rows_down > 0 else -1
        for _ in range(abs(rows_down)):
            j = k + step
            while 0 <= j < len(self.rows) and self.rows[j].start == -1:
                j += step
            if not 0 <= j < len(self.rows):
                break
            k = j
        r = self.rows[k]
        return min(r.start + col, r.end - 1)

    def keyPressEvent(self, e):
        if not self.items:
            return
        key, mods = e.key(), e.modifiers()
        mode = "range" if mods & Qt.KeyboardModifier.ShiftModifier else (
            "keep" if mods & Qt.KeyboardModifier.ControlModifier else "only")
        cur = max(0, self.current)
        per_page = max(1, self.viewport().height() // (self._cell + SPACING))
        moves = {
            Qt.Key.Key_Left: lambda: cur - 1,
            Qt.Key.Key_Right: lambda: cur + 1,
            Qt.Key.Key_Up: lambda: self._vertical(cur, -1),
            Qt.Key.Key_Down: lambda: self._vertical(cur, 1),
            Qt.Key.Key_PageUp: lambda: self._vertical(cur, -per_page),
            Qt.Key.Key_PageDown: lambda: self._vertical(cur, per_page),
            Qt.Key.Key_Home: lambda: 0,
            Qt.Key.Key_End: lambda: len(self.items) - 1,
        }
        if key in moves:
            self.set_current(moves[key](), mode if self.current >= 0 else "only")
        elif key == Qt.Key.Key_Space and mods & Qt.KeyboardModifier.ControlModifier and self.current >= 0:
            self.set_current(self.current, "toggle")
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.current >= 0:
            self.activated.emit(self.current)
        elif key == Qt.Key.Key_A and mods & Qt.KeyboardModifier.ControlModifier:
            self.select_all()
        elif key == Qt.Key.Key_Escape and self.selected:
            self.selected = set()
            self.viewport().update()
            self.selection_changed.emit()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal) and mods & Qt.KeyboardModifier.ControlModifier:
            self.set_tile_size(round(self.tile * 1.25))
        elif key == Qt.Key.Key_Minus and mods & Qt.KeyboardModifier.ControlModifier:
            self.set_tile_size(round(self.tile / 1.25))
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.viewport().update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.viewport().update()

    # --- scrubber ------------------------------------------------------------------------------------

    def month_y(self, mi: int) -> int:
        return self.rows[self._month_row[mi]].y

    def month_at_y(self, y: int) -> int:
        return self.rows[self._row_at(y)].month if self.rows else -1

    def content_height(self) -> int:
        return self._height


class Scrubber(QWidget):
    """Years down the right edge, marked where they start. Click or drag to jump; the bubble shows
    the month you'd land on. Takes the place of the scroll bar."""

    WIDTH = 56

    def __init__(self, view: TimelineView):
        super().__init__(view)
        self.view = view
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover_y = None
        self._drag = False
        self.place()

    def place(self):
        vp = self.view.viewport().geometry()
        self.setGeometry(vp.right() + 1, vp.top(), self.WIDTH, vp.height())
        self.raise_()

    def sizeHint(self):
        return QSize(self.WIDTH, 100)

    def _scale(self) -> float:
        return self.height() / max(1, self.view.content_height())

    def paintEvent(self, e):
        v = self.view
        if not v.months:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        scale = self._scale()
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 0.85)
        p.setFont(font)
        fm = p.fontMetrics()
        last_label_y = -1e9
        years_seen = set()
        for mi, m in enumerate(v.months):  # newest first, so a year's first section is its December
            if m.year in years_seen:
                continue
            years_seen.add(m.year)
            y = v.month_y(mi) * scale
            if y - last_label_y >= fm.height() + 2:  # skip labels that would overlap
                p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
                p.drawText(QRect(0, int(y), self.width() - 8, fm.height()), Qt.AlignmentFlag.AlignRight,
                           str(m.year))
                last_label_y = y
            else:
                p.fillRect(QRect(self.width() - 10, int(y) + fm.height() // 2, 4, 1),
                           pal.color(QPalette.ColorRole.PlaceholderText))
        # where the screen is now
        bar = v.verticalScrollBar()
        top = bar.value() * scale
        h = max(6.0, v.viewport().height() * scale)
        p.setPen(Qt.PenStyle.NoPen)
        hl = pal.color(QPalette.ColorRole.Highlight)
        p.setBrush(QColor(hl.red(), hl.green(), hl.blue(), 150))
        p.drawRoundedRect(self.width() - 4, int(top), 3, int(h), 1.5, 1.5)
        if self._hover_y is not None:
            p.setPen(QPen(hl, 2))
            p.drawLine(0, int(self._hover_y), self.width(), int(self._hover_y))
        p.end()

    def _label(self, y: float) -> str:
        mi = self.view.month_at_y(int(y / self._scale()))
        if mi < 0:
            return ""
        m = self.view.months[mi]
        return f"{calendar.month_name[m.month]} {m.year}" if m.month else str(m.year)

    def _jump(self, y: float):
        target = int(y / self._scale()) - self.view.viewport().height() // 2
        self.view.verticalScrollBar().setValue(target)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self._jump(e.position().y())

    def mouseMoveEvent(self, e):
        y = e.position().y()
        self._hover_y = y
        if self._drag:
            self._jump(y)
        QToolTip.showText(self.mapToGlobal(QPoint(-140, int(y) - 12)), self._label(y), self)
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = False

    def leaveEvent(self, e):
        self._hover_y = None
        QToolTip.hideText()
        self.update()

    def wheelEvent(self, e):
        self.view.wheelEvent(e)
