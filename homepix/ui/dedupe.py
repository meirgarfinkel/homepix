"""The duplicates review from the window: find the groups in the background, and move extras to the
Trash (or say they aren't duplicates) after a preview, as operations Undo takes back."""

from ..core import duplicates
from ..core.db import open_db
from ..core.settings import library_folders
from . import workers
from .fileops import plural
from .views.changes_dialog import ChangesDialog, relative
from .views.sidebar import DUPES


def _load_duplicates():
    library = library_folders()
    db = open_db()
    try:
        return duplicates.find(db), library, duplicates.count_unchecked(db)
    finally:
        db.close()


class Dedupe:
    def __init__(self, window):
        self.w = window
        self.count = ""  # groups to review, once worked out
        self._jobs = workers.Latest()

    def load(self):
        def done(res, err):
            view = self.w.dupes
            if err is not None:
                view.list.empty_text = f"Couldn't look for duplicates:\n{err}"
                view.list.viewport().update()
                return
            groups, library, pending = res
            view.set_groups(groups, library, pending)
            self.recount(view.to_decide())

        self._jobs.run(_load_duplicates, done)

    def recount(self, n: int):
        """The sidebar's count: groups to decide (not those skipped for now)."""
        self.count = n
        self.w.sidebar.set_count(DUPES, f"{n:,}" if n else "")

    def _confirm(self, title, head, note, rows, go) -> bool:
        return bool(ChangesDialog(title, head, note, rows, go, self.w).exec())

    def trash_extras(self, groups):
        """Move the groups' extras to the Trash (after a preview)."""
        library = library_folders()
        db = open_db()
        try:
            steps = duplicates.plan_extras(db, groups)
        finally:
            db.close()
        if not steps:
            return
        keeper = {c.path: g.keeper.path for g in groups for c in g.extras}
        rows = [
            (
                relative(s["src"], library),
                "Trash",
                f"A copy of {relative(keeper[s['src']], library)}" if s["src"] in keeper else "Goes with its photo",
                s["src"],
            )
            for s in steps
        ]
        n = len(steps)
        head = f"<b>{plural(n, 'file')}</b> will move to the Trash, from {plural(len(groups), 'group')} of duplicates."
        note = "Each group keeps its marked copy. Undo brings them back."
        if not self._confirm("Duplicates", head, note, rows, f"Move {plural(n, 'file')} to Trash"):
            return
        self.w.ops.run(
            "trash",
            f"Move {plural(n, 'duplicate')} to Trash",
            f"Moved {plural(n, 'duplicate')} to Trash",
            lambda db: steps,
        )

    def apart(self, groups):
        n = len(groups)
        self.w.ops.run(
            "apart",
            f"Not duplicates ({plural(n, 'group')})",
            f"Marked {plural(n, 'group')} as not duplicates",
            lambda db: [s] if (s := duplicates.plan_apart(db, groups)) is not None else [],
        )
