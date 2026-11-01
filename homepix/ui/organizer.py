"""Organizing from the window: work out the plan in the background, show it, and on the go-ahead make
it as one operation (Undo takes it back)."""

import os
from collections import Counter

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..core import organize
from ..core.db import open_db
from ..core.settings import library_folders
from . import workers
from .fileops import plural
from .views.changes_dialog import ChangesDialog, relative


def _why_text(why: str) -> str:
    if why == "renamed":
        return "Named by its date"
    if why == "with its photo":
        return "Goes with its photo"
    place = why.removeprefix("to ")
    return "No date: into Unsorted/" if place == "Unsorted" else f"Into {place}/"


def summary(plan) -> str:
    """ "940 go with their photo, 3 into Unsorted/ ..." for the files that change."""
    counts = Counter("year" if c.why.startswith("to ") and c.why != "to Unsorted" else c.why for c in plan.changes)
    parts = [
        (counts["renamed"], "named by their date"),
        (counts["year"], "into their year's folder"),
        (counts["to Unsorted"], "into Unsorted/ (no date)"),
        (counts["with its photo"], "go with their photo"),
    ]
    return ", ".join(f"{n:,} {text}" for n, text in parts if n)


def preview(plan, folders, title, parent):
    n = len(plan.changes)
    note = "Nothing changes until you choose Organize."
    if plan.not_ready:
        note += f" {plan.not_ready:,} files are skipped for now (still being checked)."
    rows = [(relative(c.src, folders), relative(c.dst, folders), _why_text(c.why), c.src) for c in plan.changes]
    return ChangesDialog(
        title,
        f"<b>{n:,} of {plan.looked_at:,} files</b> will be renamed or moved: {summary(plan)}.",
        note,
        rows,
        f"Organize {n:,} files",
        parent,
    )


def _start_plan(scope):
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
            QMessageBox.information(
                self.w,
                "Organize",
                f"{scope} isn't in your library. Add it in Settings first.",
            )
            return
        self._looking = True
        name = "the library" if scope is None else os.path.basename(scope) or scope
        self.w.statusBar().showMessage(f"Looking through {name}...")
        workers.run(_start_plan, lambda res, err: self._plan_ready(scope, name, res, err), scope)

    def _plan_ready(self, scope, name, res, err):
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
        dlg = preview(plan, folders, title, self.w)
        if not dlg.exec():
            return
        steps, n = plan.as_steps(), len(plan.changes)
        self.w.ops.run("organize", f"Organize {plural(n, 'file')}", f"Organized {plural(n, 'file')}", lambda db: steps)
