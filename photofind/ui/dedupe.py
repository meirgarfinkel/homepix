"""The duplicates review from the window: find the groups in the background, and set extras aside
(or say they aren't duplicates) after a preview, as operations Undo takes back."""

from ..core import duplicates, library_folders, open_db
from . import workers
from .fileops import plural
from .views.changes_dialog import ChangesDialog, relative
from .views.sidebar import DUPES


def _find():
    library = library_folders()
    db = open_db()
    try:
        return duplicates.find(db, library), library, duplicates.pending(db)
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
            self.count = len(groups)
            self.w.sidebar.set_count(DUPES, f"{self.count:,}" if self.count else "")
        self._jobs.run(_find, done)

    def set_aside(self, groups, to: str):
        library = library_folders()
        db = open_db()
        try:
            steps = duplicates.plan_extras(db, groups, to, library)
        finally:
            db.close()
        if not steps:
            return
        keeper = {c.path: g.keeper.path for g in groups for c in g.extras}
        rows = []
        for s in steps:
            why = (f"A copy of {relative(keeper[s['src']], library)}" if s["src"] in keeper
                   else "Goes with its photo")
            dst = relative(s["dst"], library) if s["dst"] else "Trash"
            rows.append((relative(s["src"], library), dst, why, s["src"]))
        n = len(steps)
        where = "into Duplicates/" if to == "duplicates" else "to the Trash"
        head = f"<b>{plural(n, 'file')}</b> will move {where}, from {plural(len(groups), 'group')} of duplicates."
        note = ("Each group keeps the copy marked Keep. "
                + ("They're named after the copy kept, and stay out of the timeline and search. "
                   if to == "duplicates" else "They can be restored from the Trash. ")
                + "Undo takes it all back.")
        go = f"Move {plural(n, 'file')} " + ("to Duplicates/" if to == "duplicates" else "to Trash")
        dlg = ChangesDialog("Duplicates", head, note, rows, go, self.w)
        if not dlg.exec():
            return
        if to == "duplicates":
            self.w.ops.run("duplicates", f"Set aside {plural(n, 'duplicate')}", f"Set aside {plural(n, 'duplicate')}",
                           lambda db: steps)
        else:
            self.w.ops.run("trash", f"Move {plural(n, 'duplicate')} to Trash",
                           f"Moved {plural(n, 'duplicate')} to Trash", lambda db: steps)

    def apart(self, groups):
        n = len(groups)
        self.w.ops.run("apart", f"Not duplicates ({plural(n, 'group')})", f"Marked {plural(n, 'group')} as not duplicates",
                       lambda db: [s] if (s := duplicates.plan_apart(db, groups)) is not None else [])

    def open(self, photo_id: int):
        self.w.open_photo(photo_id)

