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


def _find_aside():
    db = open_db()
    try:
        return duplicates.aside_groups(db, library_folders())
    finally:
        db.close()


class Dedupe:
    def __init__(self, window):
        self.w = window
        self.count = ""  # groups to review, once worked out
        self._jobs = workers.Latest()
        self._aside_jobs = workers.Latest()

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
            self._aside_jobs.run(_find_aside, aside_done)  # after: the first time it reads every file

        def aside_done(groups, err):
            if err is not None:
                print(f"Couldn't look through Duplicates/: {err!r}", flush=True)
                groups = []
            self.w.dupes.set_aside_groups(groups)

        self._jobs.run(_find, done)

    def _confirm(self, title, head, note, rows, go) -> bool:
        return bool(ChangesDialog(title, head, note, rows, go, self.w).exec())

    def aside_trash(self, groups):
        """Be rid of the copies in Duplicates/ (after a preview): each group's photo in the library stays,
        or the copy chosen instead takes its place."""
        library = library_folders()
        db = open_db()
        try:
            steps = duplicates.plan_aside_trash(db, groups, library)
        finally:
            db.close()
        if not steps:
            return
        keeper = {c.path: g.keeper.path for g in groups for c in g.extras}
        rows = []
        for s in steps:
            if s["action"] == "move":
                rows.append((relative(s["src"], library), relative(s["dst"], library),
                             "Kept instead of the photo in the library", s["src"]))
            elif s["src"] in keeper and not duplicates.set_aside(s["src"], library):
                rows.append((relative(s["src"], library), "Trash",
                             f"Its copy {relative(keeper[s['src']], library)} is kept instead", s["src"]))
            else:
                why = f"A copy of {relative(keeper[s['src']], library)}" if s["src"] in keeper else "Goes with its photo"
                rows.append((relative(s["src"], library), "Trash", why, s["src"]))
        gone = sum(s["action"] == "trash" for s in steps)
        head = f"<b>{plural(gone, 'file')}</b> will move to the Trash, from {plural(len(groups), 'group')}."
        note = ("The photos in your library stay (where you chose a copy instead, it takes the photo's place). "
                "They can be restored from the Trash, and Undo takes it all back.")
        if not self._confirm("Duplicates/", head, note, rows, f"Move {plural(gone, 'file')} to Trash"):
            return
        copies = f"{gone:,} cop{'ies' if gone != 1 else 'y'}"
        self.w.ops.run("trash", f"Move {copies} from Duplicates/ to Trash",
                       f"Moved {copies} from Duplicates/ to Trash", lambda db: steps)

    def put_back(self, groups):
        """Move files out of Duplicates/ back into the library (after a preview)."""
        library = library_folders()
        db = open_db()
        try:
            steps = duplicates.plan_put_back(db, groups, library)
        finally:
            db.close()
        if not steps:
            return
        rows = [(relative(s["src"], library), relative(s["dst"], library), "Back in the library", s["src"])
                for s in steps]
        n = len(steps)
        head = f"<b>{plural(n, 'file')}</b> will move out of Duplicates/, back into the library."
        note = ("Each goes to its year's folder, named by when it was taken (Unsorted/ if it has no date). "
                "Undo takes it back.")
        if not self._confirm("Duplicates/", head, note, rows, f"Move {plural(n, 'file')}"):
            return
        self.w.ops.run("move", f"Put {plural(n, 'file')} back from Duplicates/",
                       f"Put {plural(n, 'file')} back in the library", lambda db: steps)

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

