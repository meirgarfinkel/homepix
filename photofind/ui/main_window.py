"""The main window: the library sidebar, the photo grid (the timeline, a folder, or search results),
the viewer opening over it, the info panel at the side, and indexing in the status bar."""

import os
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSlider,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core import SearchIndex, add_folder, data_dir, library_folders, named_people, open_db, remove_folder
from ..core.timeline import Month, folder_counts, group_by_month, item_from_row, load_items
from . import workers
from .indexing import IndexStatus, IndexWatcher
from .thumbs import ThumbLoader
from .views.banner import Banner
from .views.info_panel import InfoPanel
from .views.people import PeopleView
from .views.settings_dialog import SettingsDialog
from .views.sidebar import ALL, PEOPLE, Sidebar, person_of, person_place
from .views.timeline import MAX_TILE, MIN_TILE, TimelineView
from .views.viewer import Viewer
from .views.welcome import Welcome


def _folder(place):
    return None if place in (ALL, PEOPLE) or person_of(place) is not None else place


def load_library(place):
    """What the grid and the sidebar show: (months, {folder: count}, total photos, named people)."""
    db = open_db()
    try:
        items = [] if place == PEOPLE else load_items(db, folder=_folder(place), person=person_of(place))
        counts = folder_counts(db)
        return group_by_month(items), counts, sum(counts.values()), named_people(db)
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


def counted(photos: int, videos: int) -> str:
    """'1,234 photos', '1 video', '1,234 photos and 56 videos'."""
    parts = [f"{n:,} {word}{'s' if n != 1 else ''}" for n, word in ((photos, "photo"), (videos, "video")) if n]
    return " and ".join(parts) or "0 photos"


class SearchBox(QLineEdit):
    focused = Signal()
    escaped = Signal()

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
        self.setWindowTitle("Photo Finder")
        # window layout and sizes, kept with the rest of Photo Finder's data
        self.settings = QSettings(str(data_dir() / "window.ini"), QSettings.Format.IniFormat)
        self.loader = ThumbLoader()
        self.watcher = IndexWatcher(self, rescan=index)
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
        self.stack = QStackedWidget()
        for w in (self.welcome, self.timeline, self.people, self.viewer):
            self.stack.addWidget(w)
        self.stack.setCurrentWidget(self.timeline)
        self.banner = Banner()
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.banner)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.sidebar = Sidebar()
        self.sidebar_dock = self._dock("Library", "library", self.sidebar, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.info = InfoPanel()
        self.info_dock = self._dock("Info", "info", self.info, Qt.DockWidgetArea.RightDockWidgetArea)

        self._build_toolbar()
        self.count_label = QLabel()
        self.statusBar().addWidget(self.count_label)
        self.statusBar().addPermanentWidget(IndexStatus(self.watcher))

        self.timeline.activated.connect(self.open_viewer)
        self.timeline.current_changed.connect(self._timeline_current)
        self.timeline.selection_changed.connect(self._timeline_current)
        self.viewer.closed.connect(self.close_viewer)
        self.viewer.current_changed.connect(lambda i: self.info.show_photo(self.viewer.items[i].id))
        self.sidebar.chosen.connect(self.show_place)
        self.people.chosen.connect(lambda pid: self.show_place(person_place(pid)))
        self.welcome.folder_chosen.connect(self.add_library_folder)
        self.banner.forget_missing.connect(self._forget_missing)
        self.watcher.state.connect(lambda s: self.banner.show_state(s.get("missing", 0), s.get("error")))
        self.watcher.library_changed.connect(self.reload)

        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1360, 860)
        state = self.settings.value("state")
        if state is not None:
            self.restoreState(state)
        self.timeline.set_tile_size(int(self.settings.value("tile", self.timeline.tile)))
        self.zoom.setValue(self.timeline.tile)
        self.reload()
        if index:
            QTimer.singleShot(1500, self.watcher.start)  # look for new photos once the window is up

    def _dock(self, title, name, widget, area) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable
                         | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(area, dock)
        return dock

    def _build_toolbar(self):
        bar = QToolBar("Main", self)
        bar.setObjectName("main")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.back_action = QAction("← Back", self)
        self.back_action.triggered.connect(self.close_viewer)
        self.back_action.setVisible(False)
        bar.addAction(self.back_action)
        folders = self.sidebar_dock.toggleViewAction()
        folders.setText("Library")
        folders.setToolTip("Show or hide the folders")
        bar.addAction(folders)
        self.search_box = SearchBox()
        self.search_box.setPlaceholderText("Search: describe what's in the photo (Ctrl+F)")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMinimumWidth(260)
        self.search_box.setMaximumWidth(520)
        self.search_box.returnPressed.connect(self.search)
        self.search_box.textChanged.connect(self._search_text_changed)
        self.search_box.focused.connect(self._warm_search)
        self.search_box.escaped.connect(self._leave_search_box)
        bar.addWidget(self.search_box)
        find = QAction("Search", self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(lambda: (self.search_box.setFocus(), self.search_box.selectAll()))
        self.addAction(find)
        bar.addSeparator()
        bar.addWidget(QLabel(" Size "))
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(MIN_TILE, MAX_TILE)
        self.zoom.setFixedWidth(120)
        self.zoom.setToolTip("Thumbnail size (Ctrl + mouse wheel, Ctrl + / Ctrl -)")
        self.zoom.valueChanged.connect(self.timeline.set_tile_size)
        bar.addWidget(self.zoom)
        info = self.info_dock.toggleViewAction()
        info.setText("Info")
        info.setShortcut(QKeySequence("I"))
        info.setToolTip("Show or hide details about the photo (I)")
        bar.addAction(info)
        settings = QAction("Settings", self)
        settings.setShortcut(QKeySequence("Ctrl+,"))
        settings.setToolTip("Library folders (Ctrl+,)")
        settings.triggered.connect(self.open_settings)
        bar.addAction(settings)
        reload = QAction("Reload", self)
        reload.setShortcut(QKeySequence.StandardKey.Refresh)
        reload.triggered.connect(self.reload)
        self.addAction(reload)
        full = QAction("Full screen", self)
        full.setShortcut(QKeySequence("F11"))
        full.triggered.connect(lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen())
        self.addAction(full)

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
        months, counts, total, people = res
        self.loaded = True
        roots = library_folders()
        self.people_names = {p["id"]: p["name"] for p in people}
        self.sidebar.set_folders(roots, counts, total, people)
        if self.place != ALL and self.sidebar.current_path() != self.place:  # that folder or person is gone
            self.place = ALL
            return self.reload(fresh=True)
        if not roots and not total:
            self._show(self.welcome)
            return
        self.people.set_people(people)
        if self.place == PEOPLE and not self.query:
            self._show(self.people)
            self.count_label.setText(f"  {len(people):,} people")
            return
        if self.stack.currentWidget() in (self.welcome, self.people):
            self._show(self.timeline)
        if self.query:
            if self._fresh:
                self._fresh = False
                self.search()
            return  # the search results stay until the search is cleared
        fresh, self._fresh = self._fresh, False
        self.timeline.empty_text = ("No photos here yet. Photo Finder is still looking through your folders."
                                    if self.watcher.indexer.snapshot()["running"] else "No photos here.")
        self.timeline.set_scrubber(True)
        self.timeline.set_months(months, fresh=fresh)
        self._count_text()

    def _place_name(self) -> str:
        """" in 2023", " of Mom", or nothing for all photos."""
        pid = person_of(self.place)
        if pid is not None:
            return f" of {self.people_names.get(pid, 'them')}"
        return "" if self.place in (ALL, PEOPLE) else f" in {Path(self.place).name}"

    def _count_text(self):
        videos = sum(it.kind == "video" for it in self.timeline.items)
        self.count_label.setText(f"  {counted(len(self.timeline.items) - videos, videos)}{self._place_name()}")

    def show_place(self, place: str):
        """Show all photos, the people page, one person's photos, or one folder (and those inside it)."""
        self.close_viewer()
        self.place = place
        self.sidebar.select(place)
        if self.query and place == PEOPLE:  # the people page isn't something to search
            self.search_box.clear()
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
        self.watcher.start()
        self.reload()

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
            if self.place != ALL and (self.place == path or self.place.startswith(path.rstrip(os.sep) + os.sep)):
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
        dlg.rescan_requested.connect(lambda: (self.watcher.start(),
                                              self.statusBar().showMessage("Looking for new photos...", 5000)))
        dlg.exec()
        self._settings_dialog = None

    def _forget_missing(self):
        n = self.banner.missing
        answer = QMessageBox.question(
            self, "Remove missing photos",
            f"Remove the {n:,} photos that can't be found from Photo Finder?\n\nOnly do this if they were "
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
        self.count_label.setText("  Searching...")
        self._searches.run(run_search, self._searched, self.search_index, q, self.place)

    def _searched(self, res, err):
        if not self.query:
            return
        if err is not None:
            self.timeline.empty_text = f"Search isn't working right now:\n{err}"
            self.timeline.set_months([], fresh=True)
            self.count_label.setText("")
            return
        sections, n, took = res
        if self.stack.currentWidget() is self.people:
            self._show(self.timeline)
        where = self._place_name()
        self.timeline.empty_text = f"No photos{where} look like “{self.query}”."
        self.timeline.set_scrubber(False)
        self.timeline.set_months(sections, fresh=True)
        self.count_label.setText(f"  {n:,} match{'es' if n != 1 else ''} for “{self.query}”{where}"
                                 f"  ({took:.2f} s)")
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
        self.zoom.setEnabled(widget is self.timeline)

    def open_viewer(self, index: int):
        self.viewer.show_items(self.timeline.items, index)
        self._show(self.viewer)
        self.viewer.setFocus()

    def close_viewer(self):
        if self.stack.currentWidget() is not self.viewer:
            return
        if self.isFullScreen():
            self.showNormal()
        i = self.viewer.index
        self._show(self.timeline)
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
        super().closeEvent(e)
