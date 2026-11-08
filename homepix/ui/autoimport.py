"""Auto import in the app: the rules' watch folders are checked a little after the window opens, then
every hour, or when asked (Check now). New files come in for rules that import by themselves, and a
bar says what came in, with See them and Undo. For the others, a bar says new files are waiting, and
Review shows them before anything is done."""

import datetime as dt

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from ..core import autoimport, fileops
from ..core.db import open_db
from ..core.settings import library_folders
from .fileops import counted, plural
from .views.review_dialog import ReviewDialog
from .views.sidebar import ALL

FIRST = 30_000  # ms after the window opens (anything interrupted is finished by then)
HOURLY = 3_600_000
RETRY = 120_000  # when something else is changing files: try again in a little while


def when(text: str | None) -> str:
    """'3:12 PM' today, 'Nov 8' before."""
    if not text:
        return ""
    t = dt.datetime.fromisoformat(text)
    if t.date() == dt.date.today():
        return t.strftime("%I:%M %p").strip().lstrip("0")
    return f"{t:%b} {t.day}"


def describe(rule: autoimport.Rule) -> str:
    """A rule's line in Settings: what it does, and how it's going."""
    if not rule.enabled:
        return "Off"
    st = autoimport.status(rule.id)
    parts = []
    if st.get("waiting"):
        parts.append(f"Waiting: {st['waiting']}")
    elif not rule.auto and st.get("pending"):
        parts.append(f"{plural(st['pending'], 'new file')} to review")
    if st.get("imported"):
        parts.append(f"last imported {plural(st.get('count', 0), 'file')}, {when(st['imported'])}")
    if st.get("checked"):
        parts.append(f"checked {when(st['checked'])}")
    else:
        parts.append("not checked yet")
    text = ", ".join(parts)
    return text[0].upper() + text[1:]


class AutoImporter(QObject):
    checked = Signal()  # the rules' statuses changed

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.timer = QTimer(self)
        self.timer.setInterval(HOURLY)
        self.timer.timeout.connect(self.check)
        self.waiting: dict[str, autoimport.Batch] = {}  # rule id: what it found, for rules that wait for review

    def start(self):
        QTimer.singleShot(FIRST, self.check)
        self.timer.start()

    def check(self, asked: bool = False):
        """Look in every watch folder now. asked: someone pressed Check now (so say what happened,
        and show progress)."""
        rules = [r for r in autoimport.load_rules() if r.enabled]
        if not rules:
            if asked:
                self.window.statusBar().showMessage("No auto import rules are on.", 5000)
            return
        folders = library_folders()

        def work(progress, should_stop):
            db = open_db()
            try:
                return autoimport.check_rules(db, rules, folders, progress=progress, should_stop=should_stop)
            finally:
                db.close()

        started = self.window.ops._in_background(
            "Checking watch folders...",
            work,
            lambda res, err: self._done(res, err, asked),
            rescan=lambda res: bool(res and res.result and res.result.unindexed),
            quiet=not asked,
        )
        if not started:
            QTimer.singleShot(RETRY, lambda: self.check(asked))

    def _done(self, res, err, asked: bool):
        self.checked.emit()
        if err is not None:
            self.window.statusBar().showMessage(f"Auto import didn't work: {err}", 12000)
            return
        for b in res.batches:
            if b.rule.auto:
                continue
            if b.imports:
                self.waiting[b.rule.id] = b
            else:
                self.waiting.pop(b.rule.id, None)
        self._show_waiting()
        self._report(res, asked)

    def _show_waiting(self):
        if not self.waiting:
            self.window.review_bar.hide()
            return
        n = sum(len(b.imports) for b in self.waiting.values())
        names = " and ".join(b.rule.name for b in self.waiting.values())
        self.window.review_bar.show_waiting(f"{plural(n, 'new file')} from {names} waiting for you to review.")

    def review(self):
        """Show what the first rule waiting found; import what's left ticked."""
        if not self.waiting:
            self.window.review_bar.hide()
            return
        rule_id, batch = next(iter(self.waiting.items()))
        dlg = ReviewDialog(batch, self.window)
        if dlg.exec() != ReviewDialog.DialogCode.Accepted:
            return
        chosen = dlg.chosen()
        left = len(batch.imports) - len(chosen.imports)

        def work(progress, should_stop):
            db = open_db()
            try:
                return autoimport.Checked([chosen], autoimport.run(db, chosen, progress, should_stop))
            finally:
                db.close()

        def done(res, err):
            if err is None:
                self.waiting.pop(rule_id, None)
                autoimport.set_status(rule_id, pending=left)
                if res.result is not None and (n := res.imported(chosen)):
                    autoimport.set_status(rule_id, imported=autoimport._now(), count=n)
            self.checked.emit()
            self._show_waiting()
            if err is not None:
                self.window.statusBar().showMessage(f"Import didn't work: {err}", 12000)
                return
            self._report(res, asked=True)

        self.window.ops._in_background(
            f"Importing from {batch.rule.name}...",
            work,
            done,
            rescan=lambda res: bool(res and res.result and res.result.unindexed),
        )

    def _report(self, res, asked: bool):
        """Say what an import did."""
        result = res.result
        if result is None or not result.finished:
            if asked:
                waiting = sum(1 for b in res.batches if not b.rule.auto and b.imports)
                self.window.statusBar().showMessage(
                    "New files are waiting for review." if waiting else "Nothing new in the watch folders.", 6000
                )
            return
        came = [(b, n) for b in res.batches if (n := res.imported(b))]
        if came:
            text = "Imported " + "; ".join(f"{counted(*res.came_in(b))} from {b.rule.name}" for b, _ in came) + "."
        else:
            text = "Cleared files that were already in your library out of the watch folders (they're in the Trash)."
        if result.skipped:
            text += f" {plural(len(result.skipped), 'file')} couldn't be brought in and will be tried again."
        place = ALL
        if len(came) == 1 and not came[0][0].rule.tidy:
            place = came[0][0].rule.dest
        self.window.import_bar.show_import(text, result.op_id, place, imported=bool(came))
        self.window._after_change("")

    def undo(self, op_id: int):
        """Undo the import the bar is about, if it's still the latest change."""
        db = open_db()
        try:
            latest = fileops.can_undo(db)
        finally:
            db.close()
        if latest is None or latest["id"] != op_id:
            QMessageBox.information(
                self.window,
                "Undo",
                "Other changes were made after this import. Undo those first (Edit > Undo), one at a time.",
            )
            return
        self.window.import_bar.hide()
        self.window.ops.undo()
