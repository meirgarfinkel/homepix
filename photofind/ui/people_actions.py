"""Naming people and fixing wrong matches. Each change is an operation like a move: it's journaled,
and Undo takes it back."""

from PySide6.QtWidgets import QInputDialog, QMessageBox

from ..core import people
from .views.faces import ask_name


def _faces(n: int) -> str:
    return f"{n:,} face{'s' if n != 1 else ''}"


class PeopleActions:
    def __init__(self, window):
        self.w = window

    def _run(self, summary, past, plan):
        self.w.ops.run("people", summary, past, lambda db: [s] if (s := plan(db)) is not None else [])

    def _names(self):
        return list(self.w.people_names.values())

    def _name_of(self, pid) -> str:
        return self.w.people_names.get(pid, "them")

    # --- faces --------------------------------------------------------------------------------------

    def name(self, face_ids, name: str):
        self._run(f"Name {_faces(len(face_ids))} {name}", f"Named {_faces(len(face_ids))} {name}",
                  lambda db: people.plan_name(db, face_ids, name))

    def ask_and_name(self, face_ids, title="Who is this?", current=""):
        name = ask_name(self.w, title, "Their name: someone already named, or someone new.", self._names(), current)
        if name and name != current:
            self.name(face_ids, name)

    def not_them(self, face_ids, pid=None):
        who = self._name_of(pid) if pid is not None else "them"
        self._run(f"Unname {_faces(len(face_ids))} (not {who})", f"Took {_faces(len(face_ids))} away from {who}",
                  lambda db: people.plan_not(db, face_ids))

    def hide(self, face_ids):
        self._run(f"Ignore {_faces(len(face_ids))}", f"Won't ask about {_faces(len(face_ids))} again",
                  lambda db: people.plan_hide(db, face_ids))

    # --- people -------------------------------------------------------------------------------------

    def rename(self, pid):
        old = self._name_of(pid)
        name = ask_name(self.w, "Rename", f"A new name for {old}. If it's someone else's name, they become one "
                        "person.", [n for n in self._names() if n != old], old)
        if not name or name == old:
            return
        self._run(f"Rename {old} to {name}", f"Renamed {old} to {name}", lambda db: people.plan_rename(db, pid, name))

    def merge(self, pid):
        old = self._name_of(pid)
        others = sorted((n for p, n in self.w.people_names.items() if p != pid), key=str.casefold)
        if not others:
            return
        into, ok = QInputDialog.getItem(self.w, "Same person", f"{old} is the same person as:", others, 0, False)
        if not ok:
            return
        target = next(p for p, n in self.w.people_names.items() if n == into)
        self._run(f"Merge {old} into {into}", f"{old} and {into} are one person now",
                  lambda db: people.plan_merge(db, [pid], target))

    def forget(self, pid):
        name = self._name_of(pid)
        answer = QMessageBox.question(
            self.w, "Forget", f"Forget {name}?\n\nTheir faces become unnamed again. The photos aren't touched, "
            "and Undo brings them back.", QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            self._run(f"Forget {name}", f"Forgot {name}", lambda db: people.plan_forget(db, pid))
