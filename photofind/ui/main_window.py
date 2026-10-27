"""The main window: the timeline, with the viewer opening over it and the info panel at the side."""

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDockWidget, QLabel, QMainWindow, QSlider, QStackedWidget, QToolBar

from ..core import open_db
from ..core.timeline import group_by_month, load_items
from . import workers
from .thumbs import ThumbLoader
from .views.info_panel import InfoPanel
from .views.timeline import MAX_TILE, MIN_TILE, TimelineView
from .views.viewer import Viewer


def load_library():
    db = open_db()
    try:
        return group_by_month(load_items(db))
    finally:
        db.close()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Photo Finder")
        self.settings = QSettings("photofind", "Photo Finder")
        self.loader = ThumbLoader()

        self.timeline = TimelineView(self.loader)
        self.timeline.empty_text = "Loading your photos..."
        self.viewer = Viewer()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.timeline)
        self.stack.addWidget(self.viewer)
        self.setCentralWidget(self.stack)

        self.info = InfoPanel()
        self.info_dock = QDockWidget("Info", self)
        self.info_dock.setObjectName("info")
        self.info_dock.setWidget(self.info)
        self.info_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable
                                   | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.info_dock)

        self._build_toolbar()
        self.count_label = QLabel()
        self.statusBar().addPermanentWidget(self.count_label)

        self.timeline.activated.connect(self.open_viewer)
        self.timeline.current_changed.connect(self._timeline_current)
        self.timeline.selection_changed.connect(self._timeline_current)
        self.viewer.closed.connect(self.close_viewer)
        self.viewer.current_changed.connect(lambda i: self.info.show_photo(self.viewer.items[i].id))

        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1280, 820)
        state = self.settings.value("state")
        if state is not None:
            self.restoreState(state)
        self.timeline.set_tile_size(int(self.settings.value("tile", self.timeline.tile)))
        self.zoom.setValue(self.timeline.tile)
        self.reload()

    def _build_toolbar(self):
        bar = QToolBar("Main", self)
        bar.setObjectName("main")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.back_action = QAction("← Back to timeline", self)
        self.back_action.triggered.connect(self.close_viewer)
        self.back_action.setVisible(False)
        bar.addAction(self.back_action)
        bar.addSeparator()
        bar.addWidget(QLabel(" Size "))
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(MIN_TILE, MAX_TILE)
        self.zoom.setFixedWidth(140)
        self.zoom.setToolTip("Thumbnail size (Ctrl + mouse wheel, Ctrl + / Ctrl -)")
        self.zoom.valueChanged.connect(self.timeline.set_tile_size)
        bar.addWidget(self.zoom)
        info = self.info_dock.toggleViewAction()
        info.setText("Info")
        info.setShortcut(QKeySequence("I"))
        info.setToolTip("Show or hide details about the photo (I)")
        bar.addAction(info)
        reload = QAction("Reload", self)
        reload.setShortcut(QKeySequence.StandardKey.Refresh)
        reload.triggered.connect(self.reload)
        self.addAction(reload)
        full = QAction("Full screen", self)
        full.setShortcut(QKeySequence("F11"))
        full.triggered.connect(lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen())
        self.addAction(full)

    # --- library -------------------------------------------------------------------------------------

    def reload(self):
        workers.run(load_library, self._loaded)

    def _loaded(self, months, err):
        if err is not None:
            self.timeline.empty_text = f"Couldn't open the photo library:\n{err}"
            self.timeline.viewport().update()
            return
        self.timeline.empty_text = ("No photos yet.\n\nAdding folders comes in the next version; "
                                    "for now, index them with the current app or `python -m photofind index`.")
        self.timeline.set_months(months)
        n = len(self.timeline.items)
        self.count_label.setText(f"{n:,} photo{'s' if n != 1 else ''}  ")

    # --- viewer --------------------------------------------------------------------------------------

    def open_viewer(self, index: int):
        self.viewer.show_items(self.timeline.items, index)
        self.stack.setCurrentWidget(self.viewer)
        self.back_action.setVisible(True)
        self.zoom.setEnabled(False)
        self.viewer.setFocus()

    def close_viewer(self):
        if self.stack.currentWidget() is not self.viewer:
            return
        if self.isFullScreen():
            self.showNormal()
        i = self.viewer.index
        self.stack.setCurrentWidget(self.timeline)
        self.back_action.setVisible(False)
        self.zoom.setEnabled(True)
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
        self.loader.stop()
        super().closeEvent(e)
