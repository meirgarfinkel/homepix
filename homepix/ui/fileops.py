"""File operations in the app: each runs on a worker thread with the library to itself (indexing
waits), shows a progress window if it takes more than a moment, and can be undone from the Edit menu."""

import os

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from ..core import fileops, open_db
from ..core.export import export
from ..core.indexer import library_lock
from . import workers


class _Progress(QObject):
    moved = Signal(int, int)  # done, total


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'s' if n != 1 else ''}"


def bad_name(name: str):
    """Why a file or folder can't have this name, or None if it can."""
    if not name.strip():
        return "The name can't be empty."
    if "/" in name or os.sep in name or "\0" in name:
        return "A name can't contain a slash."
    if name.startswith("."):
        return "A name starting with a dot would hide the file."
    if name in (".", ".."):
        return "That name is taken by the system."
    return None


class FileOps(QObject):
    """Runs the operations; the window connects:
    finished(str): a message for the status bar, after anything changed files;
    history_changed(): what Undo and Redo would do may be different now."""

    finished = Signal(str)
    history_changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.busy = False
        self._stop = False

    # --- running --------------------------------------------------------------------------------------

    def _go(self, title: str, work, finished, exclusive: bool = True, files: bool = True):
        """work(progress, should_stop) on a worker thread, then finished(result, error) here.
        exclusive: no indexing meanwhile (anything that changes the library); files: and it changes
        files, so look through the folders again afterwards."""
        if self.busy:
            self.window.statusBar().showMessage("Still busy with the last one. A moment...", 4000)
            return
        self.busy, self._stop = True, False
        watcher = self.window.watcher
        was_indexing = watcher.indexer.snapshot()["running"]
        if exclusive:
            watcher.hold()
        dlg = QProgressDialog(title, "Stop", 0, 0, self.window)
        dlg.setWindowTitle("HomePix")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(700)  # only if it takes a while
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(lambda: setattr(self, "_stop", True))
        relay = _Progress(dlg)
        relay.moved.connect(lambda done, total: (dlg.setMaximum(total), dlg.setValue(done)))

        def job():
            if not exclusive:
                return work(relay.moved.emit, lambda: self._stop)
            watcher.indexer.stop_and_wait()
            with library_lock():
                return work(relay.moved.emit, lambda: self._stop)

        def after(res, err):
            dlg.close()
            dlg.deleteLater()
            self.busy = False
            if exclusive:
                watcher.release(rescan=self.window.indexing and (files or was_indexing))
            finished(res, err)
            self.history_changed.emit()

        workers.run(job, after)

    def run(self, kind: str, summary: str, past: str, plan):
        """plan(db) -> steps (from core.fileops), worked out with the library to ourselves. summary is
        what Undo names it ("Move 3 photos to Trash"); past says it's done ("Moved 3 photos to Trash")."""
        def work(progress, should_stop):
            db = open_db()
            try:
                steps, cant = plan(db), []
                if isinstance(steps, tuple):  # (steps, [(path, why) left out])
                    steps, cant = steps
                if not steps:
                    return fileops.Result(None, skipped=cant) if cant else None
                result = fileops.run(db, kind, summary, steps, progress, should_stop)
                result.skipped = cant + result.skipped
                return result
            finally:
                db.close()

        self._go(f"{summary}...", work, lambda res, err: self._report(past, res, err), files=kind not in ("people", "apart"))

    def undo(self):
        self._go("Undoing...", lambda p, s: self._history(fileops.can_undo, fileops.undo, p),
                 lambda res, err: self._report(f"Undid: {res[0]}" if res else "", res and res[1], err))

    def redo(self):
        self._go("Redoing...", lambda p, s: self._history(fileops.can_redo, fileops.redo, p),
                 lambda res, err: self._report(f"Redid: {self._redo_name(res[0])}" if res else "",
                                               res and res[1], err))

    @staticmethod
    def _redo_name(summary: str) -> str:
        return summary[len("Undo: "):] if summary.startswith("Undo: ") else summary

    @staticmethod
    def _history(which, do, progress):
        db = open_db()
        try:
            op = which(db)
            if op is None:
                return None
            return op["summary"], do(db, progress)
        finally:
            db.close()

    def recover(self):
        """At startup: finish what a crash or power cut interrupted last time."""
        def work(progress, should_stop):
            db = open_db()
            try:
                return fileops.recover(db)
            finally:
                db.close()

        def done(notes, err):
            if err is not None:
                QMessageBox.warning(self.window, "HomePix",
                                    f"Couldn't finish what was being done to your files last time:\n{err}")
            elif notes:
                self.finished.emit("Finished what was interrupted last time: " + "; ".join(notes))

        self._go("Finishing what was interrupted last time...", work, done)

    def export(self, paths, folder, resize, quality):
        def work(progress, should_stop):
            return export(paths, folder, resize, quality, progress, should_stop)

        def done(res, err):
            if err is not None:
                QMessageBox.warning(self.window, "Export", f"Couldn't export:\n{err}")
                return
            n, failed = res
            if failed:
                self._skipped("Export", f"Exported {plural(n, 'file')}.", failed)
            self.window.statusBar().showMessage(f"Exported {plural(n, 'file')} to {folder}.", 8000)

        self._go(f"Exporting to {os.path.basename(folder) or folder}...", work, done, exclusive=False)

    # --- telling what happened ------------------------------------------------------------------------

    def _report(self, past: str, result, err):
        if err is not None:
            QMessageBox.warning(self.window, "HomePix", f"That didn't work:\n{err}")
            self.finished.emit("")
            return
        if result is None:
            self.finished.emit("")
            return
        text = past
        if result.stopped:
            text = f"Stopped partway: {plural(result.finished, 'file')} done"
        elif not result.finished:
            text = "Nothing was changed"
        if result.skipped:
            self._skipped("HomePix", text + ".", result.skipped)
        self.finished.emit(f"{text}. Ctrl+Z undoes it." if result.finished else "")

    def _skipped(self, title, text, skipped):
        lines = [f"{os.path.basename(p)}: {why}" for p, why in skipped[:12]]
        if len(skipped) > 12:
            lines.append(f"...and {len(skipped) - 12:,} more")
        QMessageBox.information(self.window, title, f"{text}\n\n{plural(len(skipped), 'file')} left as "
                                "they were:\n" + "\n".join(lines))

    @staticmethod
    def history() -> tuple[str | None, str | None]:
        """What Undo and Redo would take back, for the Edit menu."""
        db = open_db()
        try:
            u, r = fileops.can_undo(db), fileops.can_redo(db)
            redo_name = r and db.execute("SELECT summary FROM ops WHERE id=?", (r["undoes"],)).fetchone()
            return (u["summary"] if u else None), (redo_name[0] if redo_name else None)
        finally:
            db.close()
