"""Auto import in the app: the rules' watch folders are checked a little after the window opens, when
something new has finished arriving in them (they're looked at every minute; a look only lists them),
every hour whatever, or when asked (Check now). New files come in for rules that import by themselves, and a
line at the bottom of the window says what came in (Edit > Undo takes it back). For the others, a bar
says new files are waiting, and Review shows them before anything is done."""

import datetime as dt
import time

from PySide6.QtCore import QObject, QTimer, Signal

from ..core import autoimport
from ..core.db import open_db
from ..core.settings import library_folders
from . import workers
from .fileops import counted, plural
from .views.review_dialog import ReviewDialog

FIRST = 30_000  # ms after the window opens (anything interrupted is finished by then)
LOOK = 60_000  # a look at what's in the watch folders
HOURLY = 3_600_000  # a full check, new files or not
SOON = 10_000  # after a phone's file arrives (the timer restarts with each one)


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
        return "Paused"
    st = autoimport.status(rule.id)
    parts = []
    if st.get("waiting"):
        parts.append(f"Waiting: {st['waiting']}")
    elif not rule.auto and st.get("pending"):
        parts.append(f"{plural(st['pending'], 'new file')} waiting for you")
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
        self.timer.setInterval(LOOK)
        self.timer.timeout.connect(self.look)
        self._seen = None  # what the last look found
        self._checked_at = 0.0  # (time.monotonic()) of the last full check started
        self._looking = False
        self._wanted = None  # a check that found the library busy: None, or whether it was asked for
        self.waiting: dict[str, autoimport.Batch] = {}  # rule id: what it found, for rules that wait for review
        window.ops.history_changed.connect(self._library_free)

    def start(self):
        self._first = QTimer(self)
        self._first.setSingleShot(True)
        self._first.timeout.connect(self.check)
        self._first.start(FIRST)
        self.timer.start()

    def stop(self):
        """No more checks (the window closed for good)."""
        self._wanted = None
        for t in (self.timer, getattr(self, "_first", None), getattr(self, "_soon", None)):
            if t is not None:
                t.stop()

    def soon(self):
        """A phone's file has arrived: check in a few seconds (once the rest of what it's sending is in),
        so it's in the library, and on the phone, a few minutes after it was taken."""
        if not hasattr(self, "_soon"):
            self._soon = QTimer(self)
            self._soon.setSingleShot(True)
            self._soon.setInterval(SOON)
            self._soon.timeout.connect(self.check)
        self._soon.start()  # (each file that comes puts it off a little: one check for a batch)

    def look(self):
        """Every minute: a full check only when something new has finished arriving (or it's been an hour)."""
        if time.monotonic() - self._checked_at >= HOURLY / 1000:
            self.check()
            return
        rules = [r for r in autoimport.load_rules() if r.enabled]
        if not rules or self._looking:
            return
        self._looking = True

        def seen(found, err):
            self._looking = False
            if err is not None:
                print(f"Couldn't look in the import folders: {err!r}", flush=True)
                return
            if found != self._seen:
                self._seen = found
                self.check()

        workers.run(autoimport.looks, seen, rules)

    def check(self, asked: bool = False):
        """Look in every watch folder now. asked: someone pressed Check now (so say what happened,
        and show progress)."""
        self._checked_at = time.monotonic()
        rules = [r for r in autoimport.load_rules() if r.enabled]
        if not rules:
            if asked:
                self.window.statusBar().showMessage("No import automations are on.", 5000)
            return
        folders = library_folders()

        def work(progress, should_stop):
            db = open_db()
            try:
                return autoimport.check_rules(db, rules, folders, progress=progress, should_stop=should_stop)
            finally:
                db.close()

        started = self.window.ops._in_background(
            "Checking for photos to import...",
            work,
            lambda res, err: self._done(res, err, asked),
            rescan=lambda res: bool(res and res.result and res.result.unindexed),
            quiet=not asked,
            wait=False,
        )
        if not started:  # another change is under way: check when it's done
            self._wanted = bool(self._wanted) or asked

    def _library_free(self):
        if self._wanted is not None and not self.window.ops.busy:
            asked, self._wanted = self._wanted, None
            self.check(asked)

    def _done(self, res, err, asked: bool):
        self.checked.emit()
        if err is not None:
            self.window.statusBar().showMessage(f"Automatic import didn't work: {err}", 12000)
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
        self.window.review_bar.show_waiting(f"{plural(n, 'new file')} from {names} ready to import.")

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
                    "New photos are waiting for you to review." if waiting else "Nothing new to import.", 6000
                )
            return
        came = [(b, n) for b in res.batches if (n := res.imported(b))]
        if came:
            text = "Imported " + "; ".join(f"{counted(*res.came_in(b))} from {b.rule.name}" for b, _ in came) + "."
        else:
            text = "Moved files that were already in your library to the Trash."
        if result.skipped:
            text += f" {plural(len(result.skipped), 'file')} couldn't be brought in and will be tried again."
        self.window.statusBar().showMessage(text, 12000)  # (Edit > Undo takes it back)
        self.window._after_change("")
