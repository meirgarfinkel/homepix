"""The main window: the library sidebar, the photo grid (the timeline, a folder, someone's photos, or
search results), the people pages, the viewer opening over them, the info panel at the side, and
indexing in the status bar."""

import os
import time
from pathlib import Path

from PySide6.QtCore import QRect, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyleOptionSlider,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import people as known_people
from ..core.config import data_dir
from ..core.db import open_db
from ..core.people import named_people
from ..core.search import SearchIndex
from ..core.settings import (
    add_folder,
    excluded_folders,
    library_folders,
    remove_folder,
    set_excluded,
)
from ..core.timeline import Month, folder_counts, group_by_month, item_from_row, load_items
from ..core.util import within
from . import desktop, theme, workers
from . import portable as portable_ui
from .dedupe import Dedupe
from .file_actions import FileActions, count_of
from .fileops import FileOps
from .indexing import IndexStatus, IndexWatcher
from .organizer import Organizer
from .people_actions import PeopleActions
from .photo_actions import PhotoActions
from .thumbs import ThumbLoader
from .views.banner import Banner
from .views.duplicates import DuplicatesView
from .views.faces import PersonFaces, WhoView
from .views.info_panel import InfoPanel
from .views.people import PeopleView
from .views.settings_dialog import SettingsDialog
from .views.sidebar import ALL, DUPES, PEOPLE, WHO, Sidebar, faces_of, faces_place, person_of, person_place
from .views.timeline import MAX_TILE, MIN_TILE, Scrubber, TimelineView
from .views.viewer import Viewer
from .views.welcome import Welcome


def _folder(place):
    return place if os.path.isabs(place) else None


def shows_photos(place) -> bool:
    """Is it a place shown as a grid of photos (rather than a page of people or faces)?"""
    return place == ALL or person_of(place) is not None or _folder(place) is not None


def load_library(place):
    """What the grid and the sidebar show: (months, {folder: count}, total photos, named people,
    whether there are unnamed faces to ask about)."""
    db = open_db()
    try:
        excluded = excluded_folders()
        items = (
            load_items(db, folder=_folder(place), person=person_of(place), excluded=excluded)
            if shows_photos(place)
            else []
        )
        counts = folder_counts(db)
        unnamed = db.execute("SELECT EXISTS (SELECT 1 FROM faces WHERE person_id IS NULL AND hidden = 0)").fetchone()[0]
        total = sum(n for f, n in counts.items() if not within(f, excluded))
        return group_by_month(items), counts, total, named_people(db), bool(unnamed)
    finally:
        db.close()


def load_who(follow=None):
    """The "who is this?" groups, everyone's names, and (for a name just given) the faces that
    might be them too (None if not asked for)."""
    db = open_db()
    try:
        return (
            known_people.who_groups(db),
            [r[0] for r in db.execute("SELECT name FROM people")],
            known_people.follow_up(db, follow) if follow else None,
        )
    finally:
        db.close()


def load_person_faces(pid):
    db = open_db()
    try:
        return known_people.person_faces(db, pid)
    finally:
        db.close()


def load_photo_faces(photo_id):
    db = open_db()
    try:
        return known_people.face_rows(db, "f.photo_id = ? AND f.hidden = 0", (photo_id,))
    finally:
        db.close()


def load_copies(ids):
    """Those photos, in that order (any gone are left out)."""
    db = open_db()
    try:
        found = {it.id: it for it in load_items(db, f"id IN ({','.join('?' * len(ids))})", tuple(ids))}
        return [found[i] for i in ids if i in found]
    finally:
        db.close()


def load_item(photo_id):
    db = open_db()
    try:
        return load_items(db, "id = ?", (photo_id,))
    finally:
        db.close()


def run_search(index: SearchIndex, q: str, place):
    start = time.perf_counter()
    found = index.search(q, folder=_folder(place), person_id=person_of(place))
    sections = []
    for title, rows in (("Best matches", found["matches"]), ("Possibly related", found["related"])):
        if rows:
            sections.append(Month("0000-00", [item_from_row(r) for r in rows], title))
    return sections, len(found["matches"]), time.perf_counter() - start


def _gap(width: int, stretch: bool = False) -> QWidget:
    """Space in the toolbar: fixed, or taking what's left."""
    w = QWidget()
    if stretch:
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    else:
        w.setFixedWidth(width)
    return w


class JumpSlider(QSlider):
    """A slider that jumps to where it's clicked (rather than a step toward it), and drags from there."""

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            style = self.style()
            groove = style.subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
            handle = style.subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self)
            if not handle.contains(e.position().toPoint()):
                span = groove.width() - handle.width()
                x = round(e.position().x()) - groove.x() - handle.width() // 2
                self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), x, span, opt.upsideDown))
        super().mousePressEvent(e)  # (now on the handle: a drag carries on from here)


class SearchBox(QLineEdit):
    focused = Signal()
    escaped = Signal()
    icon = clear_icon = None  # drawn at the left, and (with text in) the × at the right

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.textChanged.connect(lambda _: self.update())
        self._over_clear = False

    def _clear_rect(self) -> QRect:
        return QRect(self.width() - 34, (self.height() - 24) // 2, 24, 24)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        if self.icon is not None:
            self.icon.paint(p, QRect(14, (self.height() - 18) // 2, 18, 18))
        if self.text() and self.clear_icon is not None:
            if self._over_clear:
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.color("hoverStrong"))
                p.drawEllipse(self._clear_rect())
            self.clear_icon.paint(p, self._clear_rect().adjusted(4, 4, -4, -4))
        p.end()

    def mousePressEvent(self, e):
        if self.text() and self._clear_rect().contains(e.position().toPoint()):
            self.clear()
            self.setFocus()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        over = bool(self.text()) and self._clear_rect().contains(e.position().toPoint())
        self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.IBeamCursor)
        if over != self._over_clear:
            self._over_clear = over
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self._over_clear:
            self._over_clear = False
            self.update()
        super().leaveEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focused.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
        else:
            super().keyPressEvent(e)


class MainWindow(QMainWindow):
    def __init__(self, index: bool = True):
        """index: look for new photos in the background (off for the self-test)."""
        super().__init__()
        self.setWindowTitle("HomePix")
        # window layout and sizes, kept with the rest of HomePix's data
        self.settings = QSettings(str(data_dir() / "window.ini"), QSettings.Format.IniFormat)
        self.loader = ThumbLoader()
        self.watcher = IndexWatcher(self, rescan=index)
        self.indexing = index
        self.copies = portable_ui.PortableCopies(self, active=index)
        self.ops = FileOps(self)
        self.loaded = False  # the library has been read once
        self.search_index = SearchIndex(settling=lambda: self.watcher.indexer.snapshot()["running"])
        self.place = ALL  # what the grid shows when not searching: ALL or a folder
        self.query = ""  # the search being shown, if any
        self._library = workers.Latest()
        self._searches = workers.Latest()
        self._warmed = False
        self._fresh = False  # the next reload starts at the top (something else is being shown)
        self._settings_dialog = None

        self.timeline = TimelineView(self.loader)
        self.timeline.empty_text = "Loading your photos..."
        self.viewer = Viewer()
        self.welcome = Welcome()
        self.people = PeopleView()
        self.people_names = {}  # person id -> name
        self.who = WhoView()
        self.who_count = ""  # groups to ask about, once worked out
        self.unnamed = False  # there are faces to ask about
        self.person_faces = PersonFaces()
        self.dupes = DuplicatesView()
        self._faces_jobs = workers.Latest()  # the who groups, someone's faces, or a photo's
        self._viewer_faces = workers.Latest()
        self._viewer_from = None  # the page the viewer opened over
        self.stack = QStackedWidget()
        for w in (self.welcome, self.timeline, self.people, self.who, self.person_faces, self.dupes, self.viewer):
            self.stack.addWidget(w)
        self.stack.setCurrentWidget(self.timeline)
        self.banner = Banner()
        self.page_header = QWidget()
        self.page_header.setObjectName("pageHeader")
        head = QHBoxLayout(self.page_header)
        head.setContentsMargins(24, 18, 24 + Scrubber.WIDTH, 4)
        self.page_back = QToolButton()
        self.page_back.setObjectName("pageBack")
        self.page_back.setText("People")
        self.page_back.setToolTip("Back to everyone")
        self.page_back.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.page_back.clicked.connect(lambda: self.show_place(PEOPLE))
        self.page_back.hide()
        self.page_title = QLabel("All photos")
        self.page_title.setObjectName("pageTitle")
        self.count_label = QLabel()
        self.count_label.setObjectName("pageCount")
        head.addWidget(self.page_back)
        head.addWidget(self.page_title)
        head.addStretch()
        head.addWidget(self.count_label)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.banner)
        lay.addWidget(self.page_header)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.sidebar = Sidebar()
        self.sidebar_dock = self._dock("Library", "library", self.sidebar, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.info = InfoPanel()
        self.info_dock = self._dock("Info", "info", self.info, Qt.DockWidgetArea.RightDockWidgetArea)

        self.naming = PeopleActions(self)
        self.organizer = Organizer(self)
        self.dedupe = Dedupe(self)
        self._copies_jobs = workers.Latest()  # comparing: only the group asked for last is shown
        self._copies_coming = None  # the ids of that group, until it's shown
        self._build_toolbar()
        self.files = FileActions(self)
        self.photo = PhotoActions(self)
        self._build_menus()
        self.index_status = IndexStatus(self.watcher)
        self.statusBar().addPermanentWidget(self.index_status)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().setContentsMargins(10, 0, 4, 0)
        self.statusBar().messageChanged.connect(self._status_shown)
        self.watcher.state.connect(self._status_shown)
        self._status_shown()
        theme.signals.changed.connect(self._restyle)
        self._restyle()

        self.timeline.activated.connect(self.open_viewer)
        self.timeline.current_changed.connect(self._timeline_current)
        self.timeline.selection_changed.connect(self._timeline_current)
        self.viewer.closed.connect(self.close_viewer)
        self.viewer.current_changed.connect(lambda i: self.info.show_photo(self.viewer.items[i].id))
        self.sidebar.chosen.connect(self.show_place)
        self.people.chosen.connect(lambda pid: self.show_place(person_place(pid)))
        self.people.who_chosen.connect(lambda: self.show_place(WHO))
        self.people.menu_requested.connect(self._person_menu)
        self.who.named.connect(lambda ids, name: self._answer_who(ids, lambda: self.naming.name(ids, name)))
        self.who.hidden.connect(lambda ids: self._answer_who(ids, lambda: self.naming.hide(ids)))
        self.who.confirmed.connect(
            lambda yes, no, name: self._answer_who(yes + no, lambda: self.naming.confirm(yes, no, name))
        )
        self.who.opened.connect(self._open_face_photo)
        self.person_faces.opened.connect(self._open_face_photo)
        self.dupes.trash_extras.connect(self.dedupe.trash_extras)
        self.dupes.deciding.connect(self.dedupe.recount)
        self.dupes.apart.connect(self.dedupe.apart)
        self.dupes.compared.connect(self.compare_copies)
        self.person_faces.not_them.connect(lambda ids: self.naming.not_them(ids, self.person_faces.person["id"]))
        self.person_faces.someone_else.connect(lambda ids: self.naming.ask_and_name(ids, "Who is it?"))
        self.person_faces.hidden.connect(self.naming.hide)
        self.person_faces.back.connect(lambda: self.show_place(person_place(self.person_faces.person["id"])))
        self.viewer.current_changed.connect(lambda i: self._load_viewer_faces())
        self.viewer.faces_toggled.connect(self._faces_toggled)
        self.viewer.face_clicked.connect(self._face_menu)
        self.welcome.folder_chosen.connect(self.add_library_folder)
        self.banner.forget_missing.connect(self._forget_missing)
        self.watcher.state.connect(lambda s: self.banner.show_state(s.get("missing", 0), s.get("error")))
        self.watcher.library_changed.connect(self.reload)
        self.watcher.state.connect(self._sharpening)
        self.timeline.menu_requested.connect(self.files.tile_menu)
        self.timeline.selection_changed.connect(self.files.update)
        self.sidebar.menu_requested.connect(self._folder_menu)
        QGuiApplication.clipboard().dataChanged.connect(self.files.update)
        self.ops.finished.connect(self._after_change)
        self.ops.history_changed.connect(self.files.history_changed)

        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1360, 860)
        state = self.settings.value("state")
        if state is not None:
            self.restoreState(state)
        self.timeline.set_tile_size(int(self.settings.value("tile", self.timeline.tile)))
        self.viewer.show_faces = self.settings.value("viewer/faces", "false") == "true"
        self.zoom.setValue(self.timeline.tile)
        self._recover()
        self.reload()
        self.files.history_changed()
        self.files.update()
        self.files.editor_changed()
        if index:  # look for new photos once the window is up (bringing in a folder's copy first, if waiting)
            QTimer.singleShot(1500, lambda: portable_ui.offer(self, library_folders(), self.watcher.start))
        QTimer.singleShot(2500, self._load_who)  # how many to ask about, for the sidebar
        QTimer.singleShot(5000, self.dedupe.load)  # and how many duplicates to review

    def _dock(self, title, name, widget, area) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        dock.setTitleBarWidget(QWidget())  # no title bar: the toolbar's buttons show and hide it
        self.addDockWidget(area, dock)
        return dock

    def _sharpening(self, s: dict):
        """While thumbnails are sharpened, repaint now and then: tiles on screen swap to sharper ones."""
        if s.get("phase") == "sharpening":
            self.timeline.viewport().update()

    def _status_shown(self, *_):
        """The status bar only while there's something to say."""
        s = self.index_status
        self.statusBar().setVisible(
            bool(self.statusBar().currentMessage()) or not s.label.isHidden() or not s.bar.isHidden()
        )

    def _restyle(self):
        """The icons, after the colours changed."""
        for action, name in (
            (self.back_action, "back"),
            (self.library_action, "sidebar"),
            (self.faces_action, "faces"),
            (self.info_action, "info"),
            (self.settings_action, "settings"),
            (self.more_button, "more"),
        ):
            action.setIcon(theme.icon(name))
        self.search_box.icon = theme.icon("search")
        self.search_box.clear_icon = theme.icon("clear", "muted")
        self.page_back.setIcon(theme.icon("back", "muted"))
        self.search_box.update()
        self.brand.setPixmap(theme.brand_mark())
        self.timeline.viewport().update()

    def _build_toolbar(self):
        bar = QToolBar("Main", self)
        bar.setObjectName("main")
        bar.setMovable(False)
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        bar.setIconSize(QSize(20, 20))
        bar.setFixedHeight(60)
        self.addToolBar(bar)
        bar.addWidget(_gap(6))
        self.brand = QLabel()
        bar.addWidget(self.brand)
        name = QLabel("HomePix")
        name.setObjectName("brand")
        bar.addWidget(name)
        bar.addWidget(_gap(14))
        folders = self.library_action = self.sidebar_dock.toggleViewAction()
        folders.setText("Library")
        folders.setToolTip("Show or hide the library")
        bar.addAction(folders)
        self.back_action = QAction("Back", self)
        self.back_action.setToolTip("Back to the photos (Esc)")
        self.back_action.triggered.connect(self.close_viewer)
        self.back_action.setVisible(False)
        bar.addAction(self.back_action)
        self._labelled(bar, self.back_action)
        bar.addWidget(_gap(0, stretch=True))
        self.search_box = SearchBox()
        self.search_box.setObjectName("search")
        self.search_box.setPlaceholderText("Search your photos: a dog on the beach, a red car...")
        self.search_box.setToolTip("Ctrl+F")
        self.search_box.setMinimumWidth(380)
        self.search_box.setMaximumWidth(560)
        self.search_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search_box.returnPressed.connect(self.search)
        self.search_box.textChanged.connect(self._search_text_changed)
        self.search_box.focused.connect(self._warm_search)
        self.search_box.escaped.connect(self._leave_search_box)
        bar.addWidget(self.search_box)
        bar.addWidget(_gap(0, stretch=True))
        find = QAction("Search", self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(lambda: (self.search_box.setFocus(), self.search_box.selectAll()))
        self.addAction(find)
        self.zoom = JumpSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(MIN_TILE, MAX_TILE)
        self.zoom.setFixedWidth(96)
        self.zoom.setToolTip("Thumbnail size (Ctrl + mouse wheel, Ctrl + / Ctrl -)")
        self.zoom.valueChanged.connect(self.timeline.set_tile_size)
        bar.addWidget(self.zoom)
        bar.addWidget(_gap(8))
        self.faces_action = QAction("Their faces", self)
        self.faces_action.setCheckable(True)
        self.faces_action.setToolTip("See the faces taken to be them, to take out any that aren't")
        self.faces_action.triggered.connect(self._toggle_person_faces)
        self.faces_action.setVisible(False)
        bar.addAction(self.faces_action)
        self._labelled(bar, self.faces_action)
        info = self.info_action = self.info_dock.toggleViewAction()
        info.setText("Info")
        info.setShortcut(QKeySequence("I"))
        info.setToolTip("Show or hide details about the photo (I)")
        bar.addAction(info)
        settings = self.settings_action = QAction("Settings...", self)
        settings.setShortcut(QKeySequence("Ctrl+,"))
        settings.setToolTip("Settings (Ctrl+,)")
        settings.triggered.connect(self.open_settings)
        bar.addAction(settings)
        self.more_button = QToolButton()
        self.more_button.setToolTip("More")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self.more_button)
        self.more_button.setMenu(self.more_menu)
        bar.addWidget(self.more_button)
        reload = self.reload_action = QAction("Reload", self)
        reload.setShortcut(QKeySequence("F5"))  # (Ctrl+R turns a photo)
        reload.triggered.connect(self.reload)
        self.addAction(reload)
        full = self.full_action = QAction("Full screen", self)
        full.setShortcut(QKeySequence("F11"))
        full.triggered.connect(lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen())
        self.addAction(full)

    @staticmethod
    def _labelled(bar, action):
        """A toolbar button showing its words beside the icon."""
        button = bar.widgetForAction(action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setProperty("label", True)

    def _action(self, text, slot, keys=(), tip=None) -> QAction:
        a = QAction(text, self)
        a.setShortcuts([QKeySequence(k) for k in keys])
        a.triggered.connect(slot)
        if tip:
            a.setToolTip(tip)
        self.addAction(a)  # the shortcut works wherever the focus is in the window
        return a

    def _build_menus(self):
        """Behind the toolbar's ⋯ button (every item's shortcut works anywhere in the window)."""
        bar = self.more_menu
        m = bar.addMenu("&File")
        m.addActions([self.files.open_app, self.files.show])
        m.addSeparator()
        m.addAction(self.files.export)
        m.addSeparator()
        m.addAction(
            self._action(
                "Tidy the library...",
                self.organizer.tidy,
                tip="Name every file by when it was taken, in its year's folder",
            )
        )
        m.addAction(self._action("Organize a folder...", lambda: self.organizer.choose_folder(self.open_folder())))
        m.addSeparator()
        m.addAction(self.settings_action)
        m.addSeparator()
        m.addAction(self._action("Quit", self.close, [QKeySequence.StandardKey.Quit]))
        bar.addMenu(self.files.menu(self))
        bar.addMenu(self.photo.menu(self))
        m = bar.addMenu("&View")
        m.addActions([self.library_action, self.info_action, self.full_action, self.reload_action])
        m.addSeparator()
        m.addAction(self._action("Who is this?", lambda: self.show_place(WHO), ["Ctrl+Shift+N"]))
        m.addAction(
            self._action(
                "Duplicates", lambda: self.show_place(DUPES), tip="Copies of the same photo or video, to review"
            )
        )
        m.addAction(
            self._action(
                "Show faces in the viewer",
                self.viewer.toggle_faces,
                tip="Boxes around faces, with their names (N in the viewer)",
            )
        )
        m.addSeparator()
        m.addAction(
            self._action(
                "Start faces over...",
                self.naming.start_over,
                tip="Forget every face and name, and look for the faces again",
            )
        )

    # --- working with files ----------------------------------------------------------------------------

    def open_folder(self):
        """The folder being looked at, if it's one."""
        return self.place if os.path.isabs(self.place) else None

    def _recover(self):
        db = open_db()
        try:
            interrupted = db.execute("SELECT 1 FROM ops WHERE state='running' LIMIT 1").fetchone()
        finally:
            db.close()
        if interrupted:
            self.ops.recover()

    def _after_change(self, message: str):
        if message:
            self.statusBar().showMessage(message, 12000)
        self.reload()
        if self.query:
            self._searches.run(run_search, self._searched, self.search_index, self.query, self.place)

    def _sync_viewer(self):
        """After files changed under the viewer: keep showing the same photo, or the next one if it's gone."""
        if self.stack.currentWidget() is not self.viewer:
            return
        if self.viewer.compare is not None:  # a duplicate's copies: the same ones again
            v = self.viewer
            ids, shown = [it.id for it in v.items], v.items[v.index].id

            def done(items, err):
                if err is None:
                    if len(items) < 2:
                        return self.close_viewer()
                    at = [it.id for it in items]
                    v.show_items(items, at.index(shown) if shown in at else 0)

            workers.run(load_copies, done, ids)
            return
        if self._viewer_from is not self.timeline:  # one photo, opened from a face: is it still there?
            shown = self.viewer.items[self.viewer.index]
            workers.run(
                load_item,
                lambda items, err: (self.viewer.show_items(items, 0) if items else self.close_viewer())
                if err is None
                else None,
                shown.id,
            )
            return
        if not self.timeline.items:
            return self.close_viewer()
        old = self.viewer.items[self.viewer.index].id if 0 <= self.viewer.index < len(self.viewer.items) else None
        index = {it.id: i for i, it in enumerate(self.timeline.items)}
        i = index.get(old, min(self.viewer.index, len(self.timeline.items) - 1))
        self.viewer.show_items(self.timeline.items, i)

    def _folder_menu(self, path, pos):
        if person_of(path) is not None:
            return self._person_menu(person_of(path), pos)
        menu = QMenu(self)
        mime = QGuiApplication.clipboard().mimeData()
        paste = menu.addAction("Paste here", lambda: self.files.paste_into(path))
        paste.setEnabled(mime is not None and mime.hasUrls() and os.path.isdir(path))
        selected = self.files.targets()
        if selected:
            menu.addAction(f"Move {count_of(selected)} here", lambda: self.files.into("move", selected, path))
        menu.addSeparator()
        if os.path.isdir(path):
            menu.addAction("Organize this folder...", lambda: self.organizer.preview(path))
        menu.addAction("Show in file manager", lambda: desktop.open_file(path))
        menu.addSeparator()
        menu.setToolTipsVisible(True)
        excluded = excluded_folders()
        if path in excluded:
            menu.addAction("Include in library again", lambda: self._exclude(path, False))
        elif not within(path, excluded):  # (not inside one already left out)
            ex = menu.addAction("Exclude from library", lambda: self._exclude(path, True))
            ex.setToolTip(
                "Hide its photos from All photos, people and search. Nothing is deleted, and "
                "opening the folder still shows them."
            )
        menu.exec(pos)

    def _exclude(self, path, excluded: bool):
        set_excluded(path, excluded)
        name = Path(path).name
        self.statusBar().showMessage(
            f"{name} is left out of All photos, people and search. Right-click it to include it again."
            if excluded
            else f"{name} is back in the library.",
            8000,
        )
        self.reload()

    # --- people ---------------------------------------------------------------------------------------

    def _person_menu(self, pid, pos):
        name = self.people_names.get(pid, "them")
        menu = QMenu(self)
        menu.addAction(f"{name}'s photos", lambda: self.show_place(person_place(pid)))
        menu.addAction(f"{name}'s faces", lambda: self.show_place(faces_place(pid)))
        menu.addSeparator()
        menu.addAction("Rename...", lambda: self.naming.rename(pid))
        merge = menu.addAction("Same person as...", lambda: self.naming.merge(pid))
        merge.setEnabled(len(self.people_names) > 1)
        menu.addSeparator()
        menu.addAction(f"Forget {name}...", lambda: self.naming.forget(pid))
        menu.exec(pos)

    def _face_menu(self, face, pos):
        menu = QMenu(self)
        ids, name, pid = [face["id"]], face["name"], face["person_id"]
        if name is None:
            menu.addAction("Who is this?...", lambda: self.naming.ask_and_name(ids))
        else:
            menu.addAction(f"{name}'s photos", lambda: self.show_place(person_place(pid)))
            menu.addSeparator()
            menu.addAction(f"Not {name}", lambda: self.naming.not_them(ids, pid))
            menu.addAction("Someone else...", lambda: self.naming.ask_and_name(ids, "Who is it?", name))
        menu.addAction("Not a face", lambda: self.naming.hide(ids))
        menu.exec(pos)

    def _answer_who(self, ids, do):
        """Named (or ignored) on the who page: on to the next group at once."""
        if self.ops.busy:
            self.statusBar().showMessage("Still busy with the last one. A moment...", 4000)
            return
        self.who.drop(ids)
        do()

    def _toggle_person_faces(self, on):
        pid = person_of(self.place) if person_of(self.place) is not None else faces_of(self.place)
        if pid is not None:
            self.show_place(faces_place(pid) if on else person_place(pid))

    def _load_who(self):
        def done(res, err):
            if err is not None:
                self.who.grid.empty_text = f"Couldn't find the faces to ask about:\n{err}"
                return
            groups, names, follow = res
            self.who.set_groups(groups, names, follow)
            self.who_count = self.who.remaining()
            self.people.set_who(self.who_count if self.unnamed else None)

        # (a follow-up once the naming is done, so it doesn't offer faces that naming recognized)
        self._faces_jobs.run(load_who, done, None if self.ops.busy else self.who.waiting_for)

    def _load_person_faces(self, pid):
        def done(res, err):
            if err is None and faces_of(self.place) == pid:
                self.person_faces.set_faces({"id": pid, "name": self.people_names.get(pid, "them")}, res)

        self._faces_jobs.run(load_person_faces, done, pid)

    def _load_viewer_faces(self):
        v = self.viewer
        if not v.show_faces or not 0 <= v.index < len(v.items):
            return
        photo_id = v.items[v.index].id
        self._viewer_faces.run(load_photo_faces, lambda res, err: err is None and v.set_faces(photo_id, res), photo_id)

    def _faces_toggled(self, on):
        self.settings.setValue("viewer/faces", "true" if on else "false")
        self._load_viewer_faces()
        if on:
            self.statusBar().showMessage("Click a face to name it, or to say it's someone else. N hides them.", 8000)

    def _open_face_photo(self, face):
        self.open_photo(face["photo_id"])

    def open_photo(self, photo_id: int):
        """Show one photo in the viewer, over the page shown now (it comes back when the viewer closes)."""

        def done(items, err):
            if err is None and items:
                self._viewer_from = self.stack.currentWidget()
                self.viewer.compare = None
                self.viewer.show_items(items, 0)
                self._show(self.viewer)
                self.viewer.setFocus()

        workers.run(load_item, done, photo_id)

    # --- the library ---------------------------------------------------------------------------------

    def reload(self, fresh: bool = False):
        """Load what's shown again (after indexing, or a folder was added or removed)."""
        self._fresh |= fresh
        self._library.run(load_library, self._loaded, self.place)

    def _loaded(self, res, err):
        if err is not None:
            self.timeline.empty_text = f"Couldn't open the photo library:\n{err}"
            self.timeline.viewport().update()
            return
        months, counts, total, people, self.unnamed = res
        self.loaded = True
        roots = library_folders()
        self.people_names = {p["id"]: p["name"] for p in people}
        self.sidebar.set_folders(
            roots,
            counts,
            total,
            len(people) if people or self.unnamed else None,
            self.dedupe.count if total else None,
            excluded_folders(),
        )
        pid = person_of(self.place) if person_of(self.place) is not None else faces_of(self.place)
        gone = (pid is not None and pid not in self.people_names) or (
            not self.sidebar.select(self.place) and self.place not in (WHO, DUPES)
        )
        if gone:  # that folder or person is gone
            self.place = ALL
            return self.reload(fresh=True)
        if not roots and not total:
            self._show(self.welcome)
            return
        self.people.set_people(people, self.who_count if self.unnamed else None)
        self.faces_action.setVisible(person_of(self.place) is not None or faces_of(self.place) is not None)
        self.faces_action.setChecked(faces_of(self.place) is not None)
        if shows_photos(self.place):
            self._show_photos(months)
        else:
            self._show_page(len(people))

    def _show_page(self, people: int):
        """The people page, who is this, someone's faces, or the duplicates."""
        if self.place == DUPES:
            self.dedupe.load()
        elif self.place == WHO:
            self._load_who()
        elif self.place != PEOPLE:
            self._load_person_faces(faces_of(self.place))
        self._set_count(f"  {people:,} people" if self.place == PEOPLE else "")
        if self.stack.currentWidget() is self.viewer:
            self._sync_viewer()  # (the page shows again when the viewer closes)
        else:
            self._show({PEOPLE: self.people, WHO: self.who, DUPES: self.dupes}.get(self.place, self.person_faces))

    def _show_photos(self, months):
        """The grid: all photos, a folder's, or someone's (or the search results, which stay until cleared)."""
        if self.stack.currentWidget() in (self.welcome, self.people, self.who, self.person_faces, self.dupes):
            self._show(self.timeline)
        fresh, self._fresh = self._fresh, False
        if self.query:
            if fresh:
                self.search()
            return
        self.timeline.empty_text = (
            "No photos here yet. HomePix is still looking through your folders."
            if self.watcher.indexer.snapshot()["running"]
            else "No photos here."
        )
        self.timeline.set_scrubber(True)
        self.timeline.set_months(months, fresh=fresh)
        if self.files.select_after is not None:
            if self.timeline.current < 0 and self.timeline.items:
                self.timeline.set_current(min(self.files.select_after, len(self.timeline.items) - 1))
            self.files.select_after = None
        self._sync_viewer()
        self._count_text()
        self.files.update()

    def _set_count(self, text: str):
        """The page's title (where we are) and, beside it, how much is shown."""
        self.count_label.setText(text.strip())
        pid, fid = person_of(self.place), faces_of(self.place)
        if self.query:
            title = f"“{self.query}”"
        elif pid is not None or fid is not None:
            name = self.people_names.get(pid if pid is not None else fid, "Someone")
            title = name if pid is not None else f"{name}: faces"
        else:
            title = (
                {ALL: "All photos", PEOPLE: "People", WHO: "Who is this?", DUPES: "Duplicates"}.get(self.place)
                or Path(self.place).name
                or self.place
            )
        self.page_title.setText(title)
        self.page_back.setVisible(pid is not None and not self.query)

    def _place_name(self) -> str:
        """ " in 2023", " of Mom", or nothing for all photos."""
        pid = person_of(self.place)
        if pid is not None:
            where = f" of {self.people_names.get(pid, 'them')}"
        else:
            where = "" if self.place in (ALL, PEOPLE) else f" in {Path(self.place).name}"
        return where

    def _count_text(self):
        self._set_count(f"  {count_of(self.timeline.items)}{self._place_name()}")

    def show_place(self, place: str):
        """Show all photos, the people page, one person's photos, or one folder (and those inside it)."""
        self.close_viewer()
        self.place = place
        self.sidebar.select(place)
        if self.query and not shows_photos(place):  # the people pages aren't something to search
            self.query = ""
            self.search_box.clear()
            self.reload(fresh=True)
        elif self.query:
            self.search()  # the search now looks in here
        else:
            self.reload(fresh=True)

    def add_library_folder(self, path: str):
        try:
            p = add_folder(path)
        except ValueError as e:
            QMessageBox.information(self, "Add folder", str(e))
            return
        self.statusBar().showMessage(f"Added {p}. Looking for photos in it...", 8000)
        portable_ui.offer(self, [p], self.watcher.start)  # it may hold a library from another computer
        self.reload()

    def library_brought_in(self):
        """A folder's portable copy came in: show its photos, people and duplicates."""
        self.reload(fresh=True)
        self._load_who()
        self.dedupe.load()

    def remove_library_folder(self, path: str):
        def job():  # wait for indexing to let go first, so it doesn't add the folder's photos back
            self.watcher.indexer.stop()
            t = self.watcher.indexer.thread
            if t is not None:
                t.join(timeout=60)
            return remove_folder(path)

        def done(n, err):
            if err is not None:
                QMessageBox.warning(self, "Remove folder", f"Couldn't remove {path}:\n{err}")
            else:
                self.statusBar().showMessage(f"Removed {path} ({n:,} photos) from the library.", 8000)
            if self.place != ALL and within(self.place, [path]):
                self.place = ALL
            self.reload(fresh=True)
            self.watcher.start()
            if self._settings_dialog is not None:
                self._settings_dialog.refresh()

        workers.run(job, done)

    def open_settings(self):
        dlg = SettingsDialog(self)
        self._settings_dialog = dlg

        def added(path):
            self.add_library_folder(path)
            dlg.refresh()

        dlg.add_requested.connect(added)
        dlg.remove_requested.connect(self.remove_library_folder)
        dlg.rescan_requested.connect(
            lambda: (self.watcher.start(), self.statusBar().showMessage("Looking for new photos...", 5000))
        )
        dlg.exec()
        self._settings_dialog = None
        self.files.editor_changed()

    def _forget_missing(self):
        n = self.banner.missing
        answer = QMessageBox.question(
            self,
            "Remove missing photos",
            f"Remove the {n:,} photos that can't be found from HomePix?\n\nOnly do this if they were "
            "deleted on purpose. If they're on a drive that's unplugged, plug it in instead. No files are "
            "touched either way.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.watcher.start(forget_missing=True)

    # --- search --------------------------------------------------------------------------------------

    def _warm_search(self):
        """The search model takes a second or two to load: start as soon as someone heads for the box."""
        if not self._warmed:
            self._warmed = True
            workers.run(self.search_index.warm, lambda res, err: None)

    def search(self):
        q = self.search_box.text().strip()
        if not q:
            return self._leave_search()
        self.close_viewer()
        self.query = q
        self._set_count("  Searching...")
        self._searches.run(run_search, self._searched, self.search_index, q, self.place)

    def _searched(self, res, err):
        if not self.query:
            return
        if err is not None:
            self.timeline.empty_text = f"Search isn't working right now:\n{err}"
            self.timeline.set_months([], fresh=True)
            self._set_count("")
            return
        sections, n, took = res
        if not shows_photos(self.place):
            self.place = ALL  # searching from a people page: search everything
            self.sidebar.select(ALL)
        if self.stack.currentWidget() in (self.people, self.who, self.person_faces, self.dupes):
            self._show(self.timeline)
        where = self._place_name()
        self.timeline.empty_text = f"No photos{where} look like “{self.query}”."
        self.timeline.set_scrubber(False)
        self.timeline.set_months(sections, fresh=True)
        self._sync_viewer()
        self._set_count(f"  {n:,} match{'es' if n != 1 else ''} for “{self.query}”{where}  ({took:.2f} s)")
        if self.timeline.items:
            self.timeline.setFocus()

    def _search_text_changed(self, text):
        self._warm_search()
        if not text.strip() and self.query:
            self._leave_search()

    def _leave_search(self):
        if self.query:
            self.query = ""
            self.reload(fresh=True)

    def _leave_search_box(self):
        if self.search_box.text():
            self.search_box.clear()
        else:
            self.timeline.setFocus()

    # --- viewer --------------------------------------------------------------------------------------

    def _show(self, widget):
        self.stack.setCurrentWidget(widget)
        in_viewer = widget is self.viewer
        self.back_action.setVisible(in_viewer)
        self.page_header.setVisible(widget in (self.timeline, self.people))  # the others have their own
        self.zoom.setEnabled(widget is self.timeline)
        self.files.update()

    def open_viewer(self, index: int):
        self._viewer_from = self.timeline
        self.viewer.compare = None
        self.viewer.show_items(self.timeline.items, index)
        self._show(self.viewer)
        self.viewer.setFocus()

    def compare_copies(self, ids, index):
        """A duplicate's copies, large: the arrows go from one to the next, K keeps the one shown."""

        def done(items, err):
            self._copies_coming = None
            if err is None and items:
                at = [it.id for it in items]
                first = ids[index] if ids[index] in at else at[0]
                self._viewer_from = self.dupes
                self.viewer.compare = (
                    lambda i: self.dupes.copy_info(self.viewer.items[i].id),
                    lambda i: self.dupes.keep_photo(self.viewer.items[i].id),
                    self._compare_other,
                )
                self.viewer.show_items(items, at.index(first))
                self._show(self.viewer)
                self.viewer.setFocus()

        self._copies_coming = ids
        self._copies_jobs.run(load_copies, done, ids)

    def _compare_other(self, i, step):
        """Comparing: on to the next group of duplicates (or back to the one before), still large. With
        the key held down, each step goes on from the group still loading, not the one on screen."""
        here = self._copies_coming[0] if self._copies_coming else self.viewer.items[i].id
        ids = self.dupes.other_group(here, step)
        if ids is None:
            self.statusBar().showMessage(
                "That was the last group of duplicates." if step > 0 else "That's the first group of duplicates.", 3000
            )
        else:
            self.compare_copies(ids, 0)

    def close_viewer(self):
        if self.stack.currentWidget() is not self.viewer:
            return
        self.viewer.compare = None
        if self.isFullScreen():
            self.showNormal()
        i, back = self.viewer.index, self._viewer_from or self.timeline
        self._show(back)
        if back is self.timeline:
            if i >= 0:
                self.timeline.set_current(i)  # come back to the photo you were looking at
            self.timeline.setFocus()

    def _timeline_current(self, *_):
        t = self.timeline
        if t.current >= 0 and t.current in t.selected:
            self.info.show_photo(t.items[t.current].id, len(t.selected))
        elif t.selected:
            i = max(t.selected)
            self.info.show_photo(t.items[i].id, len(t.selected))
        else:
            self.info.show_photo(None)

    def closeEvent(self, e):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState())
        self.settings.setValue("tile", self.timeline.tile)
        self.watcher.shutdown()
        self.loader.stop()
        self.dupes.stop()
        self.hide()
        self.copies.flush()  # the folders' copies match the library whenever the app is closed
        super().closeEvent(e)
