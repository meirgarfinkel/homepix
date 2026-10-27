"""Organizing from the window: work out the plan in the background, show it, and on the go-ahead make
it as one operation (Undo takes it back)."""

import os

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..core import library_folders, open_db, organize
from . import workers
from .fileops import plural
from .views.organize_dialog import OrganizeDialog


def _plan(scope):
    folders = library_folders()
    db = open_db()
    try:
        return organize.plan(db, folders, scope), folders
    finally:
        db.close()


class Organizer:
    def __init__(self, window):
        self.w = window
        self._looking = False

    def tidy(self):
        self.preview(None)

    def choose_folder(self, start=None):
        folders = library_folders()
        if not folders:
            return
        folder = QFileDialog.getExistingDirectory(self.w, "Organize a folder", start or folders[0])
        if folder:
            self.preview(os.path.abspath(folder))

    def preview(self, scope):
        """scope: a folder in the library, or None for all of it."""
        if self._looking or self.w.ops.busy:
            self.w.statusBar().showMessage("Still busy with the last one. A moment...", 4000)
            return
        if not library_folders():
            return
        if scope and organize.root_of(scope, library_folders()) is None:
            QMessageBox.information(self.w, "Organize", f"{scope} isn't in the library. Only the library's "
                                    "folders are organized; add it in Settings first.")
            return
        self._looking = True
        name = "the library" if scope is None else os.path.basename(scope) or scope
        self.w.statusBar().showMessage(f"Looking through {name}...")
        workers.run(_plan, lambda res, err: self._planned(scope, name, res, err), scope)

    def _planned(self, scope, name, res, err):
        self._looking = False
        self.w.statusBar().clearMessage()
        title = "Tidy the library" if scope is None else f"Organize {name}"
        if err is not None:
            QMessageBox.warning(self.w, title, f"Couldn't work out what to do:\n{err}")
            return
        plan, folders = res
        if not plan.changes:
            text = f"All {plural(plan.looked_at, 'file')} in {name} are named and sorted already."
            if plan.not_ready:
                text += f" ({plural(plan.not_ready, 'file')} still being indexed were left for now.)"
            QMessageBox.information(self.w, title, text)
            return
        dlg = OrganizeDialog(plan, folders, title, self.w)
        if not dlg.exec():
            return
        steps, n = plan.steps(), len(plan.changes)
        self.w.ops.run("organize", f"Organize {plural(n, 'file')}", f"Organized {plural(n, 'file')}",
                       lambda db: steps)
