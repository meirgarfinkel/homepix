"""The Edit menu and the tile menu: undo and redo, the clipboard, renaming, moving, copying, trashing,
exporting, and opening photos elsewhere. Each change is an operation (fileops.py), so Undo takes it back."""

import os

from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMenu, QMessageBox

from ..core import fileops as files
from ..core.db import open_db
from ..core.settings import library_folders, load_settings, pictures_folder
from ..core.util import within
from . import desktop
from .fileops import FileOps, bad_name, counted, plural
from .views.file_dialogs import BatchRenameDialog, ExportDialog, RenameDialog


def count_of(items) -> str:
    """'3 photos', '1 video', '3 photos and 1 video'."""
    videos = sum(it.kind == "video" for it in items)
    return counted(len(items) - videos, videos)


class FileActions:
    def __init__(self, window):
        self.w = window
        self.select_after = None  # after files went away: the index of the photo to select instead
        a, Key = window.add_action, QKeySequence.StandardKey
        self.undo = a("Undo", window.ops.undo, [Key.Undo])
        self.redo = a("Redo", window.ops.redo, ["Ctrl+Shift+Z", "Ctrl+Y"])
        self.cut = a("Cut", lambda: self.to_clipboard(cut=True), [Key.Cut])
        self.copy = a("Copy", lambda: self.to_clipboard(cut=False), [Key.Copy])
        self.paste = a("Paste", lambda: self.paste_into(window.open_folder()), [Key.Paste])
        self.select_all = a("Select all", window.timeline.select_all, [Key.SelectAll])
        self.rename = a("Rename...", self.rename_them, ["F2"])
        self.move = a("Move to...", lambda: self.into_chosen("move"), ["Ctrl+Shift+M"])
        self.new_folder = a("Move to a new folder...", self.into_new_folder)
        self.copy_to = a("Copy to...", lambda: self.into_chosen("copy"))
        self.trash = a("Move to Trash", self.trash_them, [Key.Delete])
        self.export = a("Export...", self.export_them, ["Ctrl+E"])
        self.open_app = a("Open in another app", self.open_in_app, ["Ctrl+Return"])
        self.show = a("Show in folder", self.show_in_folder)
        self.targeted = [
            self.cut,
            self.copy,
            self.rename,
            self.move,
            self.new_folder,
            self.copy_to,
            self.trash,
            self.export,
            self.open_app,
            self.show,
        ]

    def menu(self, parent) -> QMenu:
        m = QMenu("&Edit", parent)
        m.addActions([self.undo, self.redo])
        m.addSeparator()
        m.addActions([self.cut, self.copy, self.paste, self.select_all])
        m.addSeparator()
        m.addActions([self.rename, self.move, self.new_folder, self.copy_to])
        m.addSeparator()
        m.addAction(self.trash)
        return m

    def tile_menu(self, pos):
        items = self.targets()
        if not items:
            return
        menu = QMenu(self.w)
        if len(items) == 1:
            menu.addAction("Open", lambda: self.w.open_viewer(self.w.timeline.current))
        menu.addActions([self.open_app, self.show])
        menu.addSeparator()
        menu.addActions([self.cut, self.copy])
        if self.paste.isEnabled():
            menu.addAction(self.paste)
        menu.addSeparator()
        menu.addActions([self.rename, self.move, self.new_folder, self.copy_to, self.export])
        menu.addSeparator()
        self.w.photo.add_to(menu)
        menu.addSeparator()
        menu.addAction(self.trash)
        menu.exec(pos)

    # --- keeping the actions up to date -----------------------------------------------------------------

    def targets(self) -> list:
        """The photos the Edit menu works on: the one in the viewer, or those selected in the grid."""
        w = self.w
        shown = w.stack.currentWidget()
        if shown is w.viewer and 0 <= w.viewer.index < len(w.viewer.items):
            return [w.viewer.items[w.viewer.index]]
        return w.timeline.selected_items() if shown is w.timeline else []

    def update_enabled(self):
        has = bool(self.targets())
        for a in self.targeted + self.w.photo.targeted:
            a.setEnabled(has)
        self.rename.setEnabled(has or self.w.stack.currentWidget() is self.w.people)
        mime = QGuiApplication.clipboard().mimeData()
        self.paste.setEnabled(bool(self.w.open_folder()) and mime is not None and mime.hasUrls())

    def editor_changed(self):
        editor = (load_settings().get("editor") or "").strip()
        self.open_app.setText(f"Open in {os.path.basename(editor.split()[0])}" if editor else "Open in another app")

    def history_changed(self):
        try:
            undo, redo = FileOps.history()
        except Exception:
            undo = redo = None
        self.undo.setText(f"Undo {undo}" if undo else "Undo")
        self.undo.setEnabled(bool(undo))
        self.redo.setText(f"Redo {redo}" if redo else "Redo")
        self.redo.setEnabled(bool(redo))

    def _remember_place(self, items):
        """Before items may go from what's shown: select what takes their place afterwards."""
        idx = {it.id: i for i, it in enumerate(self.w.timeline.items)}
        found = [idx[it.id] for it in items if it.id in idx]
        self.select_after = min(found) if found else None

    def _status(self, text, ms):
        self.w.statusBar().showMessage(text, ms)

    # --- doing ------------------------------------------------------------------------------------------

    def to_clipboard(self, cut: bool):
        items = self.targets()
        if not items:
            return
        db = open_db()
        try:
            paths = files.with_companions(db, [it.path for it in items])
        finally:
            db.close()
        desktop.copy_files(paths, cut)
        self._status(
            f"{'Cut' if cut else 'Copied'} {count_of(items)}. Paste them into a folder here or in your file manager.",
            6000,
        )

    def paste_into(self, folder):
        if not folder:
            self._status("Choose a folder in the sidebar to paste into.", 5000)
            return
        paths, cut = desktop.clipboard_files()
        paths = [os.path.abspath(p) for p in paths if os.path.isfile(p)]
        if not paths:
            self._status("There are no files to paste. Copy some first, here or in your file manager.", 6000)
            return
        if not within(folder, library_folders()):
            return
        name, n = os.path.basename(folder) or folder, plural(len(paths), "file")
        kind = "move" if cut else "copy"
        verb, past = ("Move", "Moved") if cut else ("Copy", "Copied")
        self.w.ops.run(
            kind,
            f"{verb} {n} to {name}",
            f"{past} {n} to {name}",
            lambda db: files.plan_into(files.with_companions(db, paths), folder, kind),
        )
        if cut:
            QGuiApplication.clipboard().clear()  # they're moved: there's nothing left to paste

    def trash_them(self):
        items = self.targets()
        if not items:
            return
        paths, n = [it.path for it in items], count_of(items)
        self._remember_place(items)
        self.w.ops.run(
            "trash",
            f"Move {n} to Trash",
            f"Moved {n} to Trash",
            lambda db: files.plan_trash(files.with_companions(db, paths)),
        )

    def rename_them(self):
        w = self.w
        if w.stack.currentWidget() is w.people:
            pid = w.people.current_person()
            return w.naming.rename(pid) if pid is not None else None
        items = self.targets()
        if not items:
            return
        if len(items) == 1:
            dlg = RenameDialog(items[0].path, w)
            if not dlg.exec() or dlg.name() == os.path.basename(items[0].path):
                return
            old, new = os.path.basename(items[0].path), dlg.name()
            renames = [(items[0].path, new)]
            summary, past = f"Rename {old} to {new}", f"Renamed {old} to {new}"
        else:
            dlg = BatchRenameDialog(items, w, w.settings.value("rename/pattern", "{date} {name}"))
            if not dlg.exec():
                return
            w.settings.setValue("rename/pattern", dlg.edit.text())
            renames = list(zip([it.path for it in items], dlg.names(), strict=True))
            n = count_of(items)
            summary, past = f"Rename {n}", f"Renamed {n}"
        w.ops.run("rename", summary, past, lambda db: files.plan_rename(db, renames))

    def _start_folder(self, items) -> str:
        return self.w.open_folder() or (os.path.dirname(items[0].path) if items else str(pictures_folder()))

    def move_or_copy_into(self, kind, items, folder):
        if not within(folder, library_folders()):
            QMessageBox.information(
                self.w,
                "HomePix",
                f"{folder} isn't in your library.\n\nHomePix moves and copies photos only within your "
                "library folders (see Settings). To put copies somewhere else, use Export.",
            )
            return
        paths, n, name = [it.path for it in items], count_of(items), os.path.basename(folder) or folder
        verb, past = ("Move", "Moved") if kind == "move" else ("Copy", "Copied")
        if kind == "move":
            self._remember_place(items)
        self.w.ops.run(
            kind,
            f"{verb} {n} to {name}",
            f"{past} {n} to {name}",
            lambda db: files.plan_into(files.with_companions(db, paths), folder, kind),
        )

    def into_chosen(self, kind):
        items = self.targets()
        if not items:
            return
        title = f"{'Move' if kind == 'move' else 'Copy'} {count_of(items)} to"
        folder = QFileDialog.getExistingDirectory(self.w, title, self._start_folder(items))
        if folder:
            self.move_or_copy_into(kind, items, os.path.abspath(folder))

    def into_new_folder(self):
        items = self.targets()
        if not items:
            return
        parent = self._start_folder(items)
        name, ok = QInputDialog.getText(self.w, "Move to a new folder", f"Name of the new folder in {parent}:")
        name = name.strip()
        if not ok or not name:
            return
        problem = bad_name(name)
        if problem:
            QMessageBox.information(self.w, "Move to a new folder", problem)
            return
        self.move_or_copy_into("move", items, os.path.join(parent, name))

    def export_them(self):
        items = self.targets()
        if not items:
            return
        settings = self.w.settings
        dlg = ExportDialog(len(items), settings, self.w)
        if not dlg.exec():
            return
        folder = QFileDialog.getExistingDirectory(
            self.w, "Export to", settings.value("export/folder", str(pictures_folder()))
        )
        if not folder:
            return
        settings.setValue("export/folder", folder)
        resize, quality = dlg.choice()
        self.w.ops.export([it.path for it in items], folder, resize, quality)

    def open_in_app(self):
        editor = load_settings().get("editor")
        for it in self.targets()[:20]:
            ok = desktop.open_with(editor, it.path) if editor else desktop.open_file(it.path)
            if not ok:
                QMessageBox.warning(
                    self.w,
                    "HomePix",
                    f"Couldn't open {it.path}"
                    + (f" with “{editor}”. Check the editor in Settings." if editor else "."),
                )
                return

    def show_in_folder(self):
        items = self.targets()
        if items:
            desktop.show_in_folder(items[0].path)
