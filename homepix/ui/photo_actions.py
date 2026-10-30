"""The Photo menu: details written into the photos themselves (description, keywords, location,
date and time), turning them, and seeing everything a file holds. Each change is an
operation like a move, so Undo takes it back."""

import os

from PySide6.QtWidgets import QMenu, QMessageBox

from ..core import fileops as files
from ..core import metadata
from ..core.exiftool import available
from . import workers
from .file_actions import count_of
from .views.details_dialogs import AllDetailsDialog, DateDialog, DetailsDialog

TURNS = {
    "right": ("Rotate right", "Rotate {} right", "Rotated {} right"),
    "left": ("Rotate left", "Rotate {} left", "Rotated {} left"),
    "flip-h": ("Flip horizontally", "Flip {} horizontally", "Flipped {} horizontally"),
    "flip-v": ("Flip vertically", "Flip {} vertically", "Flipped {} vertically"),
}


def _read_details(paths):
    found = {}
    for i in range(0, len(paths), 200):
        found.update(metadata.read(paths[i : i + 200]))
    return found


class PhotoActions:
    def __init__(self, window):
        self.w = window
        a = window.add_action
        self.details = a("Edit details...", self.edit_details, ["Ctrl+I"])
        self.date = a("Date and time...", self.edit_date, ["Ctrl+Shift+D"])
        self.all_details = a("All details...", self.show_all, ["Alt+Return"])
        self.turns = {
            t: a(names[0], lambda _=False, t=t: self.turn(t), keys)
            for (t, names), keys in zip(TURNS.items(), (["Ctrl+R"], ["Ctrl+Shift+R"], [], []), strict=True)
        }
        self.targeted = [self.details, self.date, self.all_details, *self.turns.values()]

    def menu(self, parent) -> QMenu:
        m = QMenu("&Photo", parent)
        m.addActions([self.details, self.date])
        m.addSeparator()
        m.addActions(list(self.turns.values()))
        m.addSeparator()
        m.addAction(self.all_details)
        return m

    def add_to(self, menu: QMenu):
        """The tile menu's part."""
        menu.addActions([self.details, self.date])
        menu.addActions([self.turns["right"], self.turns["left"]])

    # --- doing ------------------------------------------------------------------------------------------

    def _editable_items(self):
        items = self.w.files.targets()
        if items and not available():
            QMessageBox.information(
                self.w,
                "HomePix",
                "Changing details needs ExifTool, which isn't installed. "
                "On Debian or Ubuntu: sudo apt install libimage-exiftool-perl",
            )
            return []
        return items

    def _run_edit(self, items, edit, summary, past, kind="details"):
        paths = [it.path for it in items]
        self.w.ops.run(kind, summary, past, lambda db: files.plan_details(db, paths, edit))

    def turn(self, how: str):
        items = [it for it in self._editable_items() if it.kind == "photo"]
        if not items:
            return
        _, summary, past = TURNS[how]
        what = count_of(items)
        self._run_edit(items, metadata.Edit(turn=how), summary.format(what), past.format(what))

    def edit_date(self):
        items = self._editable_items()
        if not items:
            return
        dlg = DateDialog(items, self.w)
        if not dlg.exec():
            return
        edit, what, how = dlg.edit(), count_of(items), dlg.describe()
        if edit.date is not None:
            summary, past = f"Set the date of {what} to {how}", f"Set the date of {what} to {how}"
        else:
            summary, past = f"Move the date of {what} by {how}", f"Moved the date of {what} by {how}"
        self._run_edit(items, edit, summary, past)

    def edit_details(self):
        items = self._editable_items()
        if not items:
            return
        self.w.statusBar().showMessage("Reading the details...")

        def ready(found, err):
            self.w.statusBar().clearMessage()
            if err is not None or not found:
                QMessageBox.warning(self.w, "Details", f"Couldn't read the details: {err or 'nothing readable'}")
                return
            dlg = DetailsDialog(found, self.w)
            if not dlg.exec():
                return
            edit = dlg.edit()
            if edit == metadata.Edit():
                return
            what = count_of(items)
            self._run_edit(items, edit, f"Change the details of {what}", f"Changed the details of {what}")

        workers.run(_read_details, ready, [os.path.abspath(it.path) for it in items])

    def show_all(self):
        items = self._editable_items()
        if not items:
            return
        path = items[0].path

        def ready(tags, err):
            if err is not None:
                QMessageBox.warning(self.w, "All details", f"Couldn't read them: {err}")
                return
            AllDetailsDialog(os.path.basename(path), tags, self.w).exec()

        workers.run(metadata.read_all, ready, path)
