"""The timeline: every photo by date, in month sections, with a year scrubber instead of a scroll bar.

It draws only the rows on screen, so the library's size doesn't matter. The layout is a list of
rows (a month header, or a row of tiles), each with its y position; finding what's on screen is a
binary search.
"""

import bisect
import calendar
import math
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QRubberBand, QToolTip, QWidget

from ...core.timeline import Month
from .. import theme

HEADER_H = 52
SPACING = 10
MARGIN = 24
RADIUS = 6  # photos' corners
MIN_TILE, MAX_TILE, DEFAULT_TILE = 90, 360, 170
PREFETCH_SCREENS = 1.5  # also load this many screens of thumbnails ahead and behind


def video_length(seconds) -> str:
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
        self.ticking = False  # a click ticks or unticks a photo (as on Who is this?) instead of choosing it
        # Ticking, while dragging: Who is this?'s way (Qt's list in multi-selection): the photos in the
        # rectangle swept out each flip from what they were before the press, and the rectangle shows.
        self._sweep = None  # {"from", "at": content points, "base": what was ticked before the press}
        self._band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._sweep_scroll = QTimer(self, interval=30)  # (dragged past the top or bottom: scroll on)
        self._sweep_scroll.timeout.connect(self._scroll_sweep)
        self.empty_text = ""
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setAutoFillBackground(False)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
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
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if on else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
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
        c = theme.color
        bg = c("bg")
        p.fillRect(self.viewport().rect(), bg)
        if not self.items:
            p.setPen(c("muted"))
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)
            return
        top = self.verticalScrollBar().value()
        h = self.viewport().height()
        p.translate(0, -top)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        head_font = theme.font(17, QFont.Weight.Bold)
        sub_font = QFont(self.font())
        small_font = theme.font(11, QFont.Weight.Bold)
        placeholder, accent, muted = c("field"), c("accent"), c("muted")
        corners = self._corners()
        k = self._row_at(top)
        while k < len(self.rows) and self.rows[k].y < top + h:
            r = self.rows[k]
            if r.start == -1:
                m = self.months[r.month]
                p.setPen(c("text"))
                p.setFont(head_font)
                title = m.title or (
                    f"{calendar.month_name[m.month]} {m.year}" if m.month else str(m.year or "Unknown date")
                )
                box = QRect(MARGIN, r.y + 6, self.viewport().width() - 2 * MARGIN, r.h - 6)
                p.drawText(box, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
                p.setFont(sub_font)
                p.setPen(muted)
                p.drawText(
                    box,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"{len(m.items):,} photo{'s' if len(m.items) != 1 else ''}",
                )
            else:
                for i in range(r.start, r.end):
                    it = self.items[i]
                    rect = QRect(MARGIN + (i - r.start) * (self._cell + SPACING), r.y, self._cell, self._cell)
                    img = self.loader.get(it.qhash)
                    video = it.kind == "video"
                    if img is not None:
                        p.drawImage(rect, img)
                        p.fillPath(corners.translated(rect.x(), rect.y()), bg)  # round the corners
                    else:
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(placeholder)
                        p.drawRoundedRect(QRectF(rect), RADIUS, RADIUS)
                        if self.loader.failed(it.qhash):
                            p.setPen(muted)
                            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "▶" if video else "Can't show\nthis photo")
                    if video:
                        self._draw_video_badge(p, rect, it.duration, small_font)
                    ring = QRectF(rect).adjusted(-4, -4, 4, 4)
                    if i in self.selected:
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.setPen(QPen(accent, 2))
                        p.drawRoundedRect(ring, RADIUS + 4, RADIUS + 4)
                        self._draw_check(p, rect)
                    elif i == self.current and self.hasFocus():
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.setPen(QPen(muted, 1.5, Qt.PenStyle.DashLine))
                        p.drawRoundedRect(ring, RADIUS + 4, RADIUS + 4)
            k += 1
        p.end()
        self._request_thumbs(top, h)

    def _corners(self) -> QPainterPath:
        """What's outside a tile's rounded corners, for a tile at 0, 0 (painted over in the background)."""
        if getattr(self, "_corner_cache", (None,))[0] != self._cell:
            square, rounded = QPainterPath(), QPainterPath()
            square.addRect(QRectF(-1, -1, self._cell + 2, self._cell + 2))
            rounded.addRoundedRect(QRectF(0, 0, self._cell, self._cell), RADIUS, RADIUS)
            self._corner_cache = (self._cell, square.subtracted(rounded))
        return self._corner_cache[1]

    @staticmethod
    def _draw_check(p, rect):
        """A tick in an accent circle, top left: it's selected."""
        centre = QPointF(rect.x() + 20, rect.y() + 20)
        p.setPen(QPen(theme.color("bg"), 2))
        p.setBrush(theme.color("accent"))
        p.drawEllipse(centre, 12, 12)
        tick = QPainterPath(centre + QPointF(-5, 0.5))
        tick.lineTo(centre + QPointF(-1.5, 4))
        tick.lineTo(centre + QPointF(5, -3.5))
        pen = QPen(theme.color("accentInk"), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(tick)

    @staticmethod
    def _draw_video_badge(p, rect, duration, font):
        """▶ and the length, bottom right, white on a dark pill."""
        text = f"▶ {video_length(duration)}".strip()
        p.setFont(font)
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 16, 20
        pill = QRect(rect.right() - w - 7, rect.bottom() - h - 7, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 128))
        p.drawRoundedRect(QRectF(pill), 10, 10)
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
        order = dict.fromkeys(
            items_between(top, top + h)
            + items_between(top + h, top + h + ahead)
            + items_between(top - ahead, top)[::-1]
        )  # rows on the edges come up twice
        self.loader.want([(self.items[i].qhash, self.items[i].path) for i in order])

    def _thumb_ready(self, qhash):
        self.viewport().update()

    def _on_scroll(self, _):
        self.viewport().update()
        self.scrubber.update()

    # --- input ---------------------------------------------------------------------------------------

    def set_current(self, i: int, mode: str = "only"):
        """mode: 'only' selects just i, 'toggle' (Ctrl), 'range' (Shift), 'keep' leaves the selection.
        While ticking, 'only' is 'keep': going to a photo never changes what's ticked."""
        if not self.items:
            return
        if self.ticking and mode == "only":
            mode = "keep"
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
        if self.ticking and not mods:  # a drag from here (on a photo or beside them) sweeps out a rectangle
            at = self._content_point(e.position().toPoint())
            self._sweep = {"from": at, "at": at, "base": set(self.selected)}
        if i < 0:
            if not mods and not self.ticking:  # (ticking: a click beside the photos keeps the ticks)
                self.selected = set()
                self.viewport().update()
                self.selection_changed.emit()
            return
        if mods & Qt.KeyboardModifier.ShiftModifier:
            self.set_current(i, "range")
        elif mods & Qt.KeyboardModifier.ControlModifier or self.ticking:
            self.set_current(i, "toggle")
        else:
            self.set_current(i, "only")

    # --- ticking by dragging across photos (as on Who is this?) ------------------------------------------

    def _content_point(self, pos: QPoint) -> QPoint:
        return QPoint(pos.x(), pos.y() + self.verticalScrollBar().value())

    def mouseMoveEvent(self, e):
        if self._sweep is None or not e.buttons() & Qt.MouseButton.LeftButton:
            return super().mouseMoveEvent(e)
        pos = e.position().toPoint()
        self._sweep_to(self._content_point(pos))
        h = self.viewport().height()
        if pos.y() < 0 or pos.y() > h:
            self._sweep["edge"] = pos.y() if pos.y() < 0 else pos.y() - h
            self._sweep_scroll.start()
        else:
            self._sweep_scroll.stop()

    def mouseReleaseEvent(self, e):
        self._sweep = None
        self._sweep_scroll.stop()
        self._band.hide()
        super().mouseReleaseEvent(e)

    def _scroll_sweep(self):
        if self._sweep is None:
            return self._sweep_scroll.stop()
        bar = self.verticalScrollBar()
        edge = self._sweep["edge"]
        before = bar.value()
        bar.setValue(before + max(-60, min(60, edge // 2 or (1 if edge > 0 else -1))))
        at = self._sweep["at"]
        self._sweep_to(QPoint(at.x(), at.y() + bar.value() - before))

    def _sweep_to(self, to: QPoint):
        """The photos in the rectangle from where the drag began to this point (content coordinates) each
        flip from what they were before the press; dragging back puts them back. The rectangle shows."""
        sweep = self._sweep
        sweep["at"] = to
        box = QRect(sweep["from"], to).normalized()
        self._band.setGeometry(box.translated(0, -self.verticalScrollBar().value()))
        self._band.show()
        hit = set()
        if self.rows:
            for k in range(self._row_at(box.top()), self._row_at(box.bottom()) + 1):
                r = self.rows[k]
                if r.start != -1:
                    hit.update(i for i in range(r.start, r.end) if self.item_rect(i).intersects(box))
        selected = sweep["base"] ^ hit
        under = self.item_at(QPoint(to.x(), to.y() - self.verticalScrollBar().value()))
        if under >= 0 and under != self.current:
            self.current = under
            self.current_changed.emit(under)
        if selected != self.selected:
            self.selected = selected
            self.selection_changed.emit()
        self.viewport().update()

    def contextMenuEvent(self, e):
        """Right-click a photo that isn't selected: it alone is chosen (ticking: it's ticked as well). On a
        selected one: all of them."""
        if e.reason() == e.Reason.Mouse:
            i = self.item_at(self.viewport().mapFromGlobal(e.globalPos()))
            if i >= 0 and i not in self.selected:  # (ticking: it's ticked too, and the menu is for all ticked)
                self.set_current(i, "toggle" if self.ticking else "only")
        elif self.current >= 0:  # the menu key: at the current photo
            r = self.item_rect(self.current)
            self.menu_requested.emit(self.viewport().mapToGlobal(r.center()))
            return
        self.menu_requested.emit(e.globalPos())

    def selected_items(self) -> list:
        """The selected photos in display order (or the current one if none are selected, unless
        ticking: then only what's ticked, so unticking the last one leaves nothing to act on)."""
        if self.selected or self.ticking:
            return [self.items[i] for i in sorted(self.selected)]
        return [self.items[self.current]] if 0 <= self.current < len(self.items) else []

    def mouseDoubleClickEvent(self, e):
        i = self.item_at(e.position().toPoint())
        if i >= 0 and e.button() == Qt.MouseButton.LeftButton:
            if self.ticking and not e.modifiers():  # its first click ticked it: opening it isn't ticking
                self.selected ^= {i}
                self.viewport().update()
                self.selection_changed.emit()
            self.activated.emit(i)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:  # Ctrl+wheel: bigger or smaller tiles
            steps = e.angleDelta().y() / 120
            self.set_tile_size(round(self.tile * (1.12**steps)))
        else:
            super().wheelEvent(e)

    def _rows_away(self, i: int, rows_down: int) -> int:
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
        mode = (
            "range"
            if mods & Qt.KeyboardModifier.ShiftModifier
            else ("keep" if mods & Qt.KeyboardModifier.ControlModifier or self.ticking else "only")
        )
        cur = max(0, self.current)
        per_page = max(1, self.viewport().height() // (self._cell + SPACING))
        moves = {
            Qt.Key.Key_Left: lambda: cur - 1,
            Qt.Key.Key_Right: lambda: cur + 1,
            Qt.Key.Key_Up: lambda: self._rows_away(cur, -1),
            Qt.Key.Key_Down: lambda: self._rows_away(cur, 1),
            Qt.Key.Key_PageUp: lambda: self._rows_away(cur, -per_page),
            Qt.Key.Key_PageDown: lambda: self._rows_away(cur, per_page),
            Qt.Key.Key_Home: lambda: 0,
            Qt.Key.Key_End: lambda: len(self.items) - 1,
        }
        if key in moves:
            self.set_current(moves[key](), mode if self.current >= 0 else "only")
        elif key in (others := self._keys(bool(mods & Qt.KeyboardModifier.ControlModifier))):
            others[key]()
        else:
            super().keyPressEvent(e)

    def _keys(self, ctrl: bool) -> dict:
        """What each key other than the arrows does now (a key missing here goes on to the window)."""
        K, keys = Qt.Key, {}
        if self.current >= 0:
            keys.update(dict.fromkeys((K.Key_Return, K.Key_Enter), lambda: self.activated.emit(self.current)))
            if ctrl or self.ticking:
                keys[K.Key_Space] = lambda: self.set_current(self.current, "toggle")
        if self.selected:
            keys[K.Key_Escape] = self._select_none
        if ctrl:
            keys[K.Key_A] = self.select_all
            keys.update(dict.fromkeys((K.Key_Plus, K.Key_Equal), lambda: self.set_tile_size(round(self.tile * 1.25))))
            keys[K.Key_Minus] = lambda: self.set_tile_size(round(self.tile / 1.25))
        return keys

    def _select_none(self):
        self.selected = set()
        self.viewport().update()
        self.selection_changed.emit()

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


def year_labels(starts: list, gap: float, bottom: float) -> list:
    """Where to put each year's label: at its start, moved just enough that none overlap (gap apart)
    and all fit above bottom. If even that can't fit them all, every second (third...) year is left
    out, evenly. starts: (year, section, y) top to bottom; gives the same with the label's y."""
    if not starts:
        return []
    step = max(1, math.ceil(len(starts) * gap / max(gap, bottom + gap)))
    keep = starts[::step]
    ys, prev = [], -gap
    for _, _, y in keep:  # down: no closer than gap to the one above
        prev = max(y, prev + gap)
        ys.append(prev)
    nxt = bottom + gap
    for i in range(len(ys) - 1, -1, -1):  # up: fit above bottom, still gap apart
        nxt = min(ys[i], nxt - gap)
        ys[i] = max(0.0, nxt)
    return [(year, mi, y) for (year, mi, _), y in zip(keep, ys, strict=True)]


class Scrubber(QWidget):
    """Years down the right edge, marked where they start. Click or drag to jump; the bubble shows
    the month you'd land on. Takes the place of the scroll bar."""

    WIDTH = 48

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
        p = QPainter(self)
        p.fillRect(self.rect(), theme.color("rail"))  # the faintest shade: apart from the photos
        if not v.months:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = self._scale()
        muted, accent = theme.color("muted"), theme.color("accent")
        font = theme.font(11, QFont.Weight.DemiBold)
        bold = theme.font(11, QFont.Weight.Bold)
        p.setFont(font)
        line = p.fontMetrics().height()
        shown = v.months[v.visible_months()[0]].year if v.rows else None
        self._labels = []
        for year, mi, y in year_labels(self._year_starts(scale), line + 2, self.height() - line - 4):
            now = year == shown
            p.setPen(theme.color("accentText") if now else muted)
            p.setFont(bold if now else font)
            rect = QRect(0, round(y), self.width() - 10, line)
            p.drawText(rect, Qt.AlignmentFlag.AlignRight, str(year))
            self._labels.append((rect, mi))
        # where the screen is now
        bar = v.verticalScrollBar()
        top = bar.value() * scale
        h = max(6.0, v.viewport().height() * scale)
        p.setPen(Qt.PenStyle.NoPen)
        hl = accent
        p.setBrush(QColor(hl.red(), hl.green(), hl.blue(), 150))
        p.drawRoundedRect(self.width() - 4, int(top), 3, int(h), 1.5, 1.5)
        if self._hover_y is not None:
            p.setPen(QPen(hl, 2))
            p.drawLine(0, int(self._hover_y), self.width(), int(self._hover_y))
        p.end()

    def _year_starts(self, scale: float) -> list:
        """(year, its first section, where that section starts on the scrubber), top to bottom."""
        starts, seen = [], set()
        for mi, m in enumerate(self.view.months):  # newest first, so a year's first section is its December
            if m.year not in seen:
                seen.add(m.year)
                starts.append((m.year, mi, self.view.month_y(mi) * scale))
        return starts

    def _label_at(self, y: float):
        """The section of the year label at y, if there's one there."""
        for rect, mi in getattr(self, "_labels", []):
            if rect.top() <= y <= rect.bottom():
                return mi
        return None

    def _month_label(self, y: float) -> str:
        mi = self.view.month_at_y(int(y / self._scale()))
        if mi < 0:
            return ""
        m = self.view.months[mi]
        return f"{calendar.month_name[m.month]} {m.year}" if m.month else str(m.year)

    def _jump_to(self, y: float):
        target = int(y / self._scale()) - self.view.viewport().height() // 2
        self.view.verticalScrollBar().setValue(target)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            mi = self._label_at(e.position().y())
            if mi is not None:  # a year: go to its start (labels can sit a little off from it)
                self.view.verticalScrollBar().setValue(self.view.month_y(mi))
                return
            self._drag = True
            self._jump_to(e.position().y())

    def mouseMoveEvent(self, e):
        y = e.position().y()
        self._hover_y = y
        if self._drag:
            self._jump_to(y)
        QToolTip.showText(self.mapToGlobal(QPoint(-140, int(y) - 12)), self._month_label(y), self)
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = False

    def leaveEvent(self, e):
        self._hover_y = None
        QToolTip.hideText()
        self.update()

    def wheelEvent(self, e):
        self.view.wheelEvent(e)
