"""The main window: the library sidebar, the photo grid (the timeline, a folder, or search results),
the viewer opening over it, the info panel at the side, and indexing in the status bar."""

import os
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSlider,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    SearchIndex,
    add_folder,
    data_dir,
    library_folders,
    load_settings,
    named_people,
    open_db,
    pictures_folder,
    remove_folder,
)
from ..core import fileops as files
from ..core.timeline import Month, folder_counts, group_by_month, item_from_row, load_items
from . import desktop, workers
from .fileops import FileOps, bad_name
from .indexing import IndexStatus, IndexWatcher
from .photo_actions import PhotoActions
from .thumbs import ThumbLoader
from .views.banner import Banner
from .views.file_dialogs import BatchRenameDialog, ExportDialog, RenameDialog
from .views.info_panel import InfoPanel
from .views.people import PeopleView
from .views.settings_dialog import SettingsDialog
from .views.sidebar import ALL, PEOPLE, Sidebar, person_of, person_place
from .views.timeline import MAX_TILE, MIN_TILE, TimelineView
from .views.viewer import Viewer
from .views.welcome import Welcome


def _folder(place):
    return None if place in (ALL, PEOPLE) or person_of(place) is not None else place


def load_library(place, min_rating=0):
    """What the grid and the sidebar show: (months, {folder: count}, total photos, named people)."""
    db = open_db()
    try:
        items = [] if place == PEOPLE else load_items(db, folder=_folder(place), person=person_of(place),
                                                      min_rating=min_rating)
        counts = folder_counts(db)
        return group_by_month(items), counts, sum(counts.values()), named_people(db)
    finally:
        db.close()


def run_search(index: SearchIndex, q: str, place, min_rating=0):
    start = time.perf_counter()
    found = index.search(q, folder=_folder(place), person_id=person_of(place), min_rating=min_rating)
    sections = []
    for title, rows in (("Best matches", found["matches"]), ("Possibly related", found["related"])):
        if rows:
            sections.append(Month("0000-00", [item_from_row(r) for r in rows], title))
    return sections, len(found["matches"]), time.perf_counter() - start


def inside(path: str, folders) -> bool:
    """Is path one of folders or inside one?"""
    return any(path == f or path.startswith(f.rstrip(os.sep) + os.sep) for f in folders)


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
        self.indexing = index
        self.ops = FileOps(self)
        self._select_after = None  # after files went away: the index of the photo to select instead
        self.loaded = False  # the library has been read once
        self.min_rating = 0  # show only photos with at least this many stars
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
        self._build_file_actions()
        self._build_menus()
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
        self.timeline.menu_requested.connect(self._tile_menu)
        self.timeline.selection_changed.connect(self._update_actions)
        self.sidebar.menu_requested.connect(self._folder_menu)
        QGuiApplication.clipboard().dataChanged.connect(self._update_actions)
        self.ops.finished.connect(self._after_change)
        self.ops.history_changed.connect(self._update_history)

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
        self._recover()
        self.reload()
        self._update_history()
        self._update_actions()
        self._editor_changed()
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
        folders = self.library_action = self.sidebar_dock.toggleViewAction()
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
        self.rating_filter = QComboBox()
        self.rating_filter.addItems(["Any rating"] + [f"{'★' * n} and up" if n < 5 else "★★★★★" for n in range(1, 6)])
        self.rating_filter.setToolTip("Show only photos with at least this many stars")
        self.rating_filter.currentIndexChanged.connect(self._rating_filter_changed)
        bar.addWidget(self.rating_filter)
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
        info = self.info_action = self.info_dock.toggleViewAction()
        info.setText("Info")
        info.setShortcut(QKeySequence("I"))
        info.setToolTip("Show or hide details about the photo (I)")
        bar.addAction(info)
        settings = self.settings_action = QAction("Settings...", self)
        settings.setShortcut(QKeySequence("Ctrl+,"))
        settings.setToolTip("Library folders (Ctrl+,)")
        settings.triggered.connect(self.open_settings)
        bar.addAction(settings)
        reload = self.reload_action = QAction("Reload", self)
        reload.setShortcut(QKeySequence("F5"))  # (Ctrl+R turns a photo)
        reload.triggered.connect(self.reload)
        self.addAction(reload)
        full = self.full_action = QAction("Full screen", self)
        full.setShortcut(QKeySequence("F11"))
        full.triggered.connect(lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen())
        self.addAction(full)

    def _action(self, text, slot, keys=(), tip=None) -> QAction:
        a = QAction(text, self)
        a.setShortcuts([QKeySequence(k) for k in keys])
        a.triggered.connect(slot)
        if tip:
            a.setToolTip(tip)
        self.addAction(a)  # the shortcut works wherever the focus is in the window
        return a

    def _build_file_actions(self):
        Key = QKeySequence.StandardKey
        self.undo_action = self._action("Undo", self.ops.undo, [Key.Undo])
        self.redo_action = self._action("Redo", self.ops.redo, ["Ctrl+Shift+Z", "Ctrl+Y"])
        self.cut_action = self._action("Cut", lambda: self._to_clipboard(cut=True), [Key.Cut])
        self.copy_action = self._action("Copy", lambda: self._to_clipboard(cut=False), [Key.Copy])
        self.paste_action = self._action("Paste", lambda: self._paste(self._open_folder()), [Key.Paste])
        self.select_all_action = self._action("Select all", self.timeline.select_all, [Key.SelectAll])
        self.rename_action = self._action("Rename...", self._rename, ["F2"])
        self.move_action = self._action("Move to...", lambda: self._into_chosen("move"), ["Ctrl+Shift+M"])
        self.new_folder_action = self._action("Move to a new folder...", self._into_new_folder)
        self.copy_to_action = self._action("Copy to...", lambda: self._into_chosen("copy"))
        self.trash_action = self._action("Move to Trash", self._trash, [Key.Delete])
        self.export_action = self._action("Export...", self._export, ["Ctrl+E"])
        self.open_app_action = self._action("Open in another app", self._open_in_app, ["Ctrl+Return"])
        self.show_action = self._action("Show in folder", self._show_in_folder)
        self.photo = PhotoActions(self)
        self._targeted = [self.cut_action, self.copy_action, self.rename_action, self.move_action,
                          self.new_folder_action, self.copy_to_action, self.trash_action, self.export_action,
                          self.open_app_action, self.show_action, *self.photo.targeted]

    def _build_menus(self):
        bar = self.menuBar()
        m = bar.addMenu("&File")
        m.addActions([self.open_app_action, self.show_action])
        m.addSeparator()
        m.addAction(self.export_action)
        m.addSeparator()
        m.addAction(self.settings_action)
        m.addSeparator()
        m.addAction(self._action("Quit", self.close, [QKeySequence.StandardKey.Quit]))
        m = bar.addMenu("&Edit")
        m.addActions([self.undo_action, self.redo_action])
        m.addSeparator()
        m.addActions([self.cut_action, self.copy_action, self.paste_action, self.select_all_action])
        m.addSeparator()
        m.addActions([self.rename_action, self.move_action, self.new_folder_action, self.copy_to_action])
        m.addSeparator()
        m.addAction(self.trash_action)
        bar.addMenu(self.photo.menu(self))
        m = bar.addMenu("&View")
        m.addActions([self.library_action, self.info_action, self.full_action, self.reload_action])

    # --- working with files ----------------------------------------------------------------------------

    def targets(self) -> list:
        """The photos the Edit menu works on: the one in the viewer, or those selected in the grid."""
        shown = self.stack.currentWidget()
        if shown is self.viewer and 0 <= self.viewer.index < len(self.viewer.items):
            return [self.viewer.items[self.viewer.index]]
        return self.timeline.selected_items() if shown is self.timeline else []

    @staticmethod
    def _what(items) -> str:
        videos = sum(it.kind == "video" for it in items)
        return counted(len(items) - videos, videos)

    def _open_folder(self):
        """The folder being looked at, if it's one."""
        return self.place if os.path.isabs(self.place) else None

    def _update_actions(self):
        has = bool(self.targets())
        for a in self._targeted:
            a.setEnabled(has)
        mime = QGuiApplication.clipboard().mimeData()
        self.paste_action.setEnabled(bool(self._open_folder()) and mime is not None and mime.hasUrls())

    def _editor_changed(self):
        editor = (load_settings().get("editor") or "").strip()
        self.open_app_action.setText(f"Open in {os.path.basename(editor.split()[0])}" if editor
                                     else "Open in another app")

    def _update_history(self):
        try:
            undo, redo = FileOps.history()
        except Exception:
            undo = redo = None
        self.undo_action.setText(f"Undo {undo}" if undo else "Undo")
        self.undo_action.setEnabled(bool(undo))
        self.redo_action.setText(f"Redo {redo}" if redo else "Redo")
        self.redo_action.setEnabled(bool(redo))

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
            self._searches.run(run_search, self._searched, self.search_index, self.query, self.place,
                               self.min_rating)

    def _sync_viewer(self):
        """After files changed under the viewer: keep showing the same photo, or the next one if it's gone."""
        if self.stack.currentWidget() is not self.viewer:
            return
        if not self.timeline.items:
            return self.close_viewer()
        old = self.viewer.items[self.viewer.index].id if 0 <= self.viewer.index < len(self.viewer.items) else None
        index = {it.id: i for i, it in enumerate(self.timeline.items)}
        i = index.get(old, min(self.viewer.index, len(self.timeline.items) - 1))
        self.viewer.show_items(self.timeline.items, i)

    def _remember_place(self, items):
        """Before items may go from what's shown: select what takes their place afterwards."""
        idx = {it.id: i for i, it in enumerate(self.timeline.items)}
        found = [idx[it.id] for it in items if it.id in idx]
        self._select_after = min(found) if found else None

    def _to_clipboard(self, cut: bool):
        items = self.targets()
        if not items:
            return
        db = open_db()
        try:
            paths = files.with_companions(db, [it.path for it in items])
        finally:
            db.close()
        desktop.copy_files(paths, cut)
        what = self._what(items)
        self.statusBar().showMessage(f"{'Cut' if cut else 'Copied'} {what}. Paste them into a folder here or in "
                                     "your file manager.", 6000)

    def _paste(self, folder):
        if not folder:
            self.statusBar().showMessage("Choose a folder in the sidebar to paste into.", 5000)
            return
        paths, cut = desktop.clipboard_files()
        paths = [os.path.abspath(p) for p in paths if os.path.isfile(p)]
        if not paths:
            self.statusBar().showMessage("There are no files to paste. Copy some first, here or in your file "
                                         "manager.", 6000)
            return
        if not inside(folder, library_folders()):
            return
        name = os.path.basename(folder) or folder
        what = f"{len(paths):,} file{'s' if len(paths) != 1 else ''}"
        kind = "move" if cut else "copy"
        verb, past = ("Move", "Moved") if cut else ("Copy", "Copied")
        self.ops.run(kind, f"{verb} {what} to {name}", f"{past} {what} to {name}",
                     lambda db: files.plan_into(files.with_companions(db, paths), folder, kind))
        if cut:
            QGuiApplication.clipboard().clear()  # they're moved: there's nothing left to paste

    def _trash(self):
        items = self.targets()
        if not items:
            return
        paths, what = [it.path for it in items], self._what(items)
        self._remember_place(items)
        self.ops.run("trash", f"Move {what} to Trash", f"Moved {what} to Trash",
                     lambda db: files.plan_trash(files.with_companions(db, paths)))

    def _rename(self):
        items = self.targets()
        if not items:
            return
        if len(items) == 1:
            dlg = RenameDialog(items[0].path, self)
            if not dlg.exec() or dlg.name() == os.path.basename(items[0].path):
                return
            old, new = os.path.basename(items[0].path), dlg.name()
            renames = [(items[0].path, new)]
            summary, past = f"Rename {old} to {new}", f"Renamed {old} to {new}"
        else:
            dlg = BatchRenameDialog(items, self, self.settings.value("rename/pattern", "{date} {name}"))
            if not dlg.exec():
                return
            self.settings.setValue("rename/pattern", dlg.edit.text())
            renames = list(zip([it.path for it in items], dlg.names(), strict=True))
            what = self._what(items)
            summary, past = f"Rename {what}", f"Renamed {what}"
        self.ops.run("rename", summary, past, lambda db: files.plan_rename(db, renames))

    def _start_folder(self, items) -> str:
        return self._open_folder() or (os.path.dirname(items[0].path) if items else str(pictures_folder()))

    def _into(self, kind, items, folder):
        roots = library_folders()
        if not inside(folder, roots):
            QMessageBox.information(
                self, "Photo Finder",
                f"{folder} isn't in your library.\n\nPhoto Finder moves and copies photos only within your "
                "library folders (see Settings). To put copies somewhere else, use Export.")
            return
        paths, what, name = [it.path for it in items], self._what(items), os.path.basename(folder) or folder
        verb, past = ("Move", "Moved") if kind == "move" else ("Copy", "Copied")
        if kind == "move":
            self._remember_place(items)
        self.ops.run(kind, f"{verb} {what} to {name}", f"{past} {what} to {name}",
                     lambda db: files.plan_into(files.with_companions(db, paths), folder, kind))

    def _into_chosen(self, kind):
        items = self.targets()
        if not items:
            return
        title = f"{'Move' if kind == 'move' else 'Copy'} {self._what(items)} to"
        folder = QFileDialog.getExistingDirectory(self, title, self._start_folder(items))
        if folder:
            self._into(kind, items, os.path.abspath(folder))

    def _into_new_folder(self):
        items = self.targets()
        if not items:
            return
        parent = self._start_folder(items)
        name, ok = QInputDialog.getText(self, "Move to a new folder", f"Name of the new folder in {parent}:")
        name = name.strip()
        if not ok or not name:
            return
        problem = bad_name(name)
        if problem:
            QMessageBox.information(self, "Move to a new folder", problem)
            return
        self._into("move", items, os.path.join(parent, name))

    def _export(self):
        items = self.targets()
        if not items:
            return
        dlg = ExportDialog(len(items), self.settings, self)
        if not dlg.exec():
            return
        start = self.settings.value("export/folder", str(pictures_folder()))
        folder = QFileDialog.getExistingDirectory(self, "Export to", start)
        if not folder:
            return
        self.settings.setValue("export/folder", folder)
        resize, quality = dlg.choice()
        self.ops.export([it.path for it in items], folder, resize, quality)

    def _open_in_app(self):
        items = self.targets()[:20]
        editor = load_settings().get("editor")
        for it in items:
            ok = desktop.open_with(editor, it.path) if editor else desktop.open_file(it.path)
            if not ok:
                QMessageBox.warning(self, "Photo Finder", f"Couldn't open {it.path}"
                                    + (f" with “{editor}”. Check the editor in Settings." if editor else "."))
                return

    def _show_in_folder(self):
        items = self.targets()
        if items:
            desktop.show_in_folder(items[0].path)

    def _tile_menu(self, pos):
        items = self.targets()
        if not items:
            return
        menu = QMenu(self)
        if len(items) == 1:
            menu.addAction("Open", lambda: self.open_viewer(self.timeline.current))
        menu.addActions([self.open_app_action, self.show_action])
        menu.addSeparator()
        menu.addActions([self.cut_action, self.copy_action])
        if self.paste_action.isEnabled():
            menu.addAction(self.paste_action)
        menu.addSeparator()
        menu.addActions([self.rename_action, self.move_action, self.new_folder_action, self.copy_to_action,
                         self.export_action])
        menu.addSeparator()
        self.photo.add_to(menu)
        menu.addSeparator()
        menu.addAction(self.trash_action)
        menu.exec(pos)

    def _folder_menu(self, path, pos):
        menu = QMenu(self)
        mime = QGuiApplication.clipboard().mimeData()
        paste = menu.addAction("Paste here", lambda: self._paste(path))
        paste.setEnabled(mime is not None and mime.hasUrls() and os.path.isdir(path))
        selected = self.targets()
        if selected:
            menu.addAction(f"Move {self._what(selected)} here", lambda: self._into("move", selected, path))
        menu.addSeparator()
        menu.addAction("Show in file manager", lambda: desktop.open_file(path))
        menu.exec(pos)

    # --- the library ---------------------------------------------------------------------------------

    def reload(self, fresh: bool = False):
        """Load what's shown again (after indexing, or a folder was added or removed)."""
        self._fresh |= fresh
        self._library.run(load_library, self._loaded, self.place, self.min_rating)

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
        if self._select_after is not None:
            if self.timeline.current < 0 and self.timeline.items:
                self.timeline.set_current(min(self._select_after, len(self.timeline.items) - 1))
            self._select_after = None
        self._sync_viewer()
        self._count_text()
        self._update_actions()

    def _rating_filter_changed(self, n: int):
        self.min_rating = n
        if self.query:
            self.search()
        else:
            self.reload(fresh=True)

    def _place_name(self) -> str:
        """" in 2023", " of Mom", or nothing for all photos."""
        pid = person_of(self.place)
        if pid is not None:
            where = f" of {self.people_names.get(pid, 'them')}"
        else:
            where = "" if self.place in (ALL, PEOPLE) else f" in {Path(self.place).name}"
        if self.min_rating:
            where += f" rated {'★' * self.min_rating}{' and up' if self.min_rating < 5 else ''}"
        return where

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
        self._editor_changed()

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
        self._searches.run(run_search, self._searched, self.search_index, q, self.place, self.min_rating)

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
        self._sync_viewer()
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
        self._update_actions()

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
