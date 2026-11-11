"""Settings, in sections (one panel each): the folders that make up the library, the folders excluded
from it (exclude a folder, take one back in), import automations, the app to edit photos in, and the
theme."""

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core import autoimport, background, phones
from ...core.settings import (
    excluded_folders,
    library_folders,
    load_settings,
    pictures_folder,
    save_settings,
    set_excluded,
)
from ...core.util import within
from .. import theme
from ..autoimport import describe, when
from .rule_dialog import RuleDialog


class SettingsDialog(QDialog):
    add_requested = Signal(str)
    remove_requested = Signal(str)
    rescan_requested = Signal()
    left_out_changed = Signal()  # a folder excluded, or taken back in
    import_check_requested = Signal()  # Check now, for import automations
    background_changed = Signal()  # Keep running in the background, turned on or off

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(720, 820)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        page.setObjectName("settingsPage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)  # (between the sections)
        scroll.setWidget(page)
        outer.addWidget(scroll, 1)

        # --- the library ---
        card = self._card(lay, "Library folders", "The folders HomePix shows and searches.")
        self.list = self._folder_list(rows=3)
        self.list.itemSelectionChanged.connect(self._selected_folder)
        card.addWidget(self.list)
        add = QPushButton("Add a folder...")
        add.clicked.connect(self._add_folder)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove_folder)
        rescan = QPushButton("Check for new photos now")
        rescan.setObjectName("checkButton")
        rescan.clicked.connect(self.rescan_requested)
        card.addLayout(self._buttons([add, self.remove], [rescan]))

        card = self._card(
            lay,
            "Excluded folders",
            "Folders whose photos don't show in All photos, people or search. "
            "They still show when you open the folder itself.",
        )
        self.left_list = self._folder_list(rows=2)
        self.left_list.itemSelectionChanged.connect(self._selected_folder)
        card.addWidget(self.left_list)
        self.leave_button = QPushButton("Exclude a folder...")
        self.leave_button.clicked.connect(self._leave_out)
        self.back_button = QPushButton("Take back in")
        self.back_button.clicked.connect(self._take_back)
        card.addLayout(self._buttons([self.leave_button, self.back_button]))

        # --- bringing photos in ---
        card = self._card(
            lay,
            "Import automations",
            "Import new photos by themselves from a folder your sync app fills (from a phone, say). "
            "Checked every minute while HomePix is running.",
        )
        self.rule_list = self._folder_list(rows=2)
        self.rule_list.itemSelectionChanged.connect(self._selected_folder)
        self.rule_list.itemDoubleClicked.connect(lambda _: self._edit_rule())
        card.addWidget(self.rule_list)
        self.add_rule_button = QPushButton("New automation...")
        self.add_rule_button.clicked.connect(lambda: self._edit_rule(new=True))
        self.edit_rule_button = QPushButton("Edit...")
        self.edit_rule_button.clicked.connect(lambda: self._edit_rule())
        self.remove_rule_button = QPushButton("Remove")
        self.remove_rule_button.clicked.connect(self._remove_rule)
        self.check_now = QPushButton("Check now")
        self.check_now.setObjectName("checkButton")
        self.check_now.clicked.connect(self.import_check_requested)
        card.addLayout(
            self._buttons([self.add_rule_button, self.edit_rule_button, self.remove_rule_button], [self.check_now])
        )
        line = QFrame()
        line.setObjectName("settingsRule")
        line.setFixedHeight(1)
        card.addSpacing(4)
        card.addWidget(line)
        card.addSpacing(4)
        self.keep_running = QCheckBox("Keep running in the background")
        self.keep_running.setChecked(background.on())
        self.keep_running.setToolTip(
            "HomePix starts hidden when you log in, and closing the window leaves it running, so new photos "
            "come in with the window closed. File > Quit stops it until you next log in or open it."
        )
        self.keep_running.toggled.connect(self._keep_running)
        card.addWidget(self.keep_running)
        card.addWidget(self._hint("Starts hidden when you log in, so new photos come in with the window closed."))

        # --- phones with the HomePix app ---
        card = self._card(
            lay,
            "Phones",
            "Phones with the HomePix app send their new photos here by themselves, over your home network "
            "(encrypted, and only to this computer). They go in through an import automation.",
        )
        self.phone_list = self._folder_list(rows=1)
        self.phone_list.itemSelectionChanged.connect(self._selected_folder)
        card.addWidget(self.phone_list)
        self.pair_button = QPushButton("Pair a phone...")
        self.pair_button.clicked.connect(self._pair)
        self.unpair_button = QPushButton("Unpair")
        self.unpair_button.clicked.connect(self._unpair)
        card.addLayout(self._buttons([self.pair_button, self.unpair_button]))

        # --- the rest ---
        card = self._card(lay, "Editing photos", "The app that Open in... uses.")
        self.editor = QLineEdit(load_settings().get("editor", ""))
        self.editor.setPlaceholderText("The usual app for each kind of file")
        self.editor.setToolTip(
            "The app that Open in... uses, e.g. gimp or darktable (the photo is added at "
            "the end, or where %f is). Leave it empty for the usual app."
        )
        self.editor.editingFinished.connect(self._save_editor)
        form = QHBoxLayout()
        form.setSpacing(12)
        form.addWidget(QLabel("Open photos in:"))
        form.addWidget(self.editor, 1)
        card.addLayout(form)

        card = self._card(lay, "Appearance")
        self.look = QComboBox()
        for key, name in theme.MODES.items():
            self.look.addItem(name, key)
        self.look.setCurrentIndex(self.look.findData(theme.mode()))
        self.look.currentIndexChanged.connect(lambda _: theme.set_mode(self.look.currentData()))
        look = QHBoxLayout()
        look.setSpacing(12)
        look.addWidget(QLabel("Colours:"))
        look.addWidget(self.look)
        look.addStretch()
        card.addLayout(look)
        lay.addStretch()

        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)  # (none of them is the main one)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        bar = QWidget()
        bar.setObjectName("settingsBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(20, 12, 20, 16)
        row.addWidget(buttons)
        outer.addWidget(bar)
        self.refresh()

    # --- building the sections ---

    @staticmethod
    def _hint(text: str) -> QLabel:
        note = QLabel(text)
        note.setObjectName("hint")
        note.setWordWrap(True)
        return note

    def _card(self, page, title: str, note: str = "") -> QVBoxLayout:
        """A section: a panel with its title (and what it's for) at the top. Returns its layout."""
        frame = QFrame()
        frame.setObjectName("settingsCard")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(18, 16, 18, 18)
        lay.setSpacing(10)
        head = QLabel(title)
        head.setObjectName("settingsTitle")
        font = head.font()
        font.setPointSizeF(font.pointSizeF() * 1.25)
        font.setWeight(QFont.Weight.DemiBold)
        head.setFont(font)
        lay.addWidget(head)
        if note:
            lay.addWidget(self._hint(note))
            lay.addSpacing(2)
        page.addWidget(frame)
        return lay

    ROW = 38  # a list row's height, in pixels

    @staticmethod
    def _folder_list(rows: int) -> QListWidget:
        """A list as tall as what's in it (rows at least, 5 at most, then it scrolls); a long path is
        shortened in the middle (the whole of it in its tooltip)."""
        lst = QListWidget()
        lst.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lst.setProperty("rows", rows)
        SettingsDialog._fit(lst)
        return lst

    @staticmethod
    def _fit(lst: QListWidget):
        shown = min(max(lst.count(), lst.property("rows")), 5)
        lst.setFixedHeight(shown * SettingsDialog.ROW + 10)

    @staticmethod
    def _buttons(left, right=()) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        for b in left:
            row.addWidget(b)
        row.addStretch()
        for b in right:
            row.addWidget(b)
        return row

    def _keep_running(self, want: bool):
        why = background.set_on(want)
        self.background_changed.emit()
        if why:
            QMessageBox.warning(
                self, "Keep running in the background", f"HomePix couldn't be set to start when you log in:\n{why}"
            )

    def _save_editor(self):
        s = load_settings()
        s["editor"] = self.editor.text().strip()
        save_settings(s)

    def done(self, r):
        self._save_editor()
        super().done(r)

    def refresh(self):
        self.list.clear()
        for f in library_folders():
            self._add_item(self.list, f if Path(f).is_dir() else f"{f}   (not connected right now)", f)
        keep = self._chosen(self.left_list)
        self.left_list.clear()
        for f in excluded_folders():
            self._add_item(self.left_list, f, f, keep)
        self._fit(self.list)
        self._fit(self.left_list)
        self.refresh_rules()

    def refresh_rules(self):
        keep = self._chosen(self.rule_list)
        self.rule_list.clear()
        for r in autoimport.load_rules():
            item = QListWidgetItem(theme.icon("folder", "muted"), f"{r.name}  ·  {describe(r)}")
            item.setData(Qt.ItemDataRole.UserRole, r.id)
            item.setToolTip(f"Import from {r.watch}\nImport to {'the year folders in ' if r.tidy else ''}{r.dest}")
            self.rule_list.addItem(item)
            if r.id == keep:
                item.setSelected(True)
        self._fit(self.rule_list)
        self.refresh_phones()

    def refresh_phones(self):
        keep = self._chosen(self.phone_list)
        self.phone_list.clear()
        names = {r.id: r.name for r in autoimport.load_rules()}
        for p in phones.load_phones():
            to = names.get(p.rule_id)
            where = f"into {to}" if to else "nowhere: its automation was removed"
            sent = f"last sent {when(p.sent)}" if p.sent else "nothing sent yet"
            item = QListWidgetItem(theme.icon("phone", "muted"), f"{p.name}  ·  {where}  ·  {sent}")
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            item.setToolTip(f"Paired {p.paired}" + (f"\nLast in touch {p.seen}" if p.seen else ""))
            self.phone_list.addItem(item)
            if p.id == keep:
                item.setSelected(True)
        self._fit(self.phone_list)
        self._selected_folder()

    def _pair(self):
        from .pair_dialog import PairDialog

        folders = library_folders()
        if not folders:
            QMessageBox.information(self, "Pair a phone", "Add a library folder first: the phone's photos go into it.")
            return
        link = getattr(self.parent(), "phone_link", None)
        if link is None:
            return
        dlg = PairDialog(link, autoimport.load_rules(), folders, self)
        dlg.exec()
        excluded = dlg.excluded_now
        dlg.deleteLater()
        self.refresh()  # (a new automation, and maybe its folder excluded)
        if excluded:
            self.left_out_changed.emit()

    def _unpair(self):
        chosen = self._chosen(self.phone_list)
        phone = next((p for p in phones.load_phones() if p.id == chosen), None)
        if phone is None:
            return
        rule = next((r for r in autoimport.load_rules() if r.id == phone.rule_id), None) if phone.own_rule else None
        waiting = rule is not None and bool(autoimport.settled_files(rule.watch, now=float("inf")))
        also = f'\n\nIts automation "{rule.name}" is removed too.' if rule is not None and not waiting else ""
        answer = QMessageBox.question(
            self,
            "Unpair",
            f'Unpair "{phone.name}"? It stops sending photos here. Photos already sent stay in the library.{also}'
            "\n\nTo send again, pair it again.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            phones.forget(phone.id)
            if also:
                autoimport.save_rules([r for r in autoimport.load_rules() if r.id != rule.id])
            link = getattr(self.parent(), "phone_link", None)
            if link is not None:
                link.refresh()
            self.refresh_rules()  # (and the phones)

    def _edit_rule(self, new: bool = False):
        folders = library_folders()
        if not folders:
            QMessageBox.information(
                self, "Import automations", "Add a library folder first: imported photos go into it."
            )
            return
        rules = autoimport.load_rules()
        chosen = None if new else self._chosen(self.rule_list)
        rule = next((r for r in rules if r.id == chosen), None)
        if not new and rule is None:
            return
        dlg = RuleDialog(rule, rules, folders, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        rules = [dlg.rule if r.id == dlg.rule.id else r for r in rules]
        if new:
            rules.append(dlg.rule)
        autoimport.save_rules(rules)
        if dlg.excluded_now:
            self.refresh()  # (the folder is in Excluded folders now too)
            self.left_out_changed.emit()
        else:
            self.refresh_rules()

    def _remove_rule(self):
        chosen = self._chosen(self.rule_list)
        rules = autoimport.load_rules()
        rule = next((r for r in rules if r.id == chosen), None)
        if rule is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove automation",
            f'Remove "{rule.name}"?\n\nNo photos are touched.',
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            autoimport.save_rules([r for r in rules if r.id != rule.id])
            self.refresh_rules()

    @staticmethod
    def _add_item(lst, text, path, keep=None):
        home = str(Path.home())
        if text.startswith(home + os.sep):
            text = "~" + text[len(home) :]  # (the home folder, the short way)
        item = QListWidgetItem(theme.icon("folder", "muted"), text)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        lst.addItem(item)
        if path == keep:
            item.setSelected(True)

    @staticmethod
    def _chosen(lst) -> str | None:
        items = lst.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _selected_folder(self):
        self.remove.setEnabled(bool(self.list.selectedItems()))
        self.back_button.setEnabled(self._chosen(self.left_list) is not None)
        self.leave_button.setEnabled(bool(library_folders()))
        if hasattr(self, "rule_list"):
            chosen = self._chosen(self.rule_list) is not None
            self.edit_rule_button.setEnabled(chosen)
            self.remove_rule_button.setEnabled(chosen)
        if hasattr(self, "phone_list"):
            self.unpair_button.setEnabled(self._chosen(self.phone_list) is not None)

    def _leave_out(self):
        roots = library_folders()
        start = self._chosen(self.list) or (roots[0] if roots else str(pictures_folder()))
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder to exclude", start)
        if not chosen:
            return
        chosen = os.path.abspath(chosen)
        if chosen in map(os.path.abspath, roots):
            why = "That's a whole library folder. To stop showing it, remove it from Library folders above."
        elif not within(chosen, roots):
            why = "That folder isn't in the library, so there's nothing to exclude."
        elif within(chosen, excluded_folders()):
            why = "That folder is already excluded."
        else:
            why = None
        if why:
            QMessageBox.information(self, "Exclude a folder", why)
            return
        set_excluded(chosen, True)
        self.refresh()
        self.left_out_changed.emit()

    def _take_back(self):
        path = self._chosen(self.left_list)
        if path is not None:
            set_excluded(path, False)
            self.refresh()
            self.left_out_changed.emit()

    def _add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a folder with photos", str(pictures_folder()))
        if path:
            self.add_requested.emit(path)

    def _remove_folder(self):
        items = self.list.selectedItems()
        if not items:
            return
        folder = items[0].data(Qt.ItemDataRole.UserRole)
        answer = QMessageBox.question(
            self,
            "Remove folder",
            f"Remove {folder} from the library?\n\nYour files aren't touched.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.remove_requested.emit(folder)
