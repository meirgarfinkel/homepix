"""Opening a photo from outside: "Open With > HomePix" in the file manager, or a photo dropped on the
app's icon. The photo is shown large, and the arrows go through the other photos in its folder. One
that isn't in the library yet is looked for first (its folder is added, if the user agrees)."""

import os

from PySide6.QtWidgets import QMessageBox

from ..core.db import open_db
from ..core.library import MEDIA_EXTS
from ..core.settings import library_folders
from ..core.timeline import load_items
from ..core.util import within
from . import workers

# what the applications menu says HomePix opens (the file manager offers it for these)
MIME_TYPES = [
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/gif",
    "image/heic",
    "image/heif",
    "image/x-adobe-dng",
    "image/x-dcraw",
    "video/mp4",
    "video/quicktime",
    "video/x-m4v",
]


def given_files(args) -> list[str]:
    """The photos and videos among the program's arguments."""
    found = []
    for a in args:
        if a.startswith("file://"):
            from urllib.parse import unquote, urlparse

            a = unquote(urlparse(a).path)
        if os.path.isfile(a) and os.path.splitext(a)[1].lower() in MEDIA_EXTS:
            found.append(os.path.abspath(a))
    return found


def photos_in(folder: str):
    """The photos right in folder (not below it), newest first."""
    db = open_db()
    try:
        return [it for it in load_items(db, folder=folder) if os.path.dirname(it.path) == folder]
    finally:
        db.close()


def folder_items(path: str):
    """The photos in path's folder, and where path is among them; or None if it isn't in the library."""
    items, real = photos_in(os.path.dirname(path)), os.path.realpath(path)
    for i, it in enumerate(items):
        if it.path == path or os.path.realpath(it.path) == real:
            return items, i
    return None


class Opener:
    def __init__(self, window):
        self.window = window
        self.waiting = None  # a photo being looked for: opened when indexing finds it

    def open(self, paths):
        """Show the first of paths (more aren't opened at once: the arrows go through the folder)."""
        paths = given_files(paths)
        if not paths:
            return
        path = paths[0]
        self.window.bring_forward()
        workers.run(folder_items, lambda res, err: self._found(path, res, err), path)

    def library_changed(self):
        """Indexing found something: is it the photo waiting to be opened?"""
        if self.waiting:
            path = self.waiting
            workers.run(
                folder_items, lambda res, err: res and self.waiting == path and self._found(path, res, err), path
            )

    def _found(self, path, res, err):
        w = self.window
        if err is not None:
            QMessageBox.warning(w, "HomePix", f"Couldn't open {os.path.basename(path)}:\n{err}")
            return
        if res is not None:
            self.waiting = None
            items, index = res
            w.show_folder_in_viewer(os.path.dirname(path), items, index)
            return
        if self.waiting == path:
            return  # still looking
        name, folder = os.path.basename(path), os.path.dirname(path)
        if within(folder, library_folders()):
            self.waiting = path  # a new photo in a library folder: indexing will find it
            w.statusBar().showMessage(f"Looking for {name} in your library...", 8000)
            w.watcher.start()
            return
        ask = QMessageBox.question(
            w,
            "HomePix",
            f"{name} isn't in your library.\n\nAdd its folder, {folder}, to your library? "
            "HomePix will look through it and then show the photo.",
        )
        if ask == QMessageBox.StandardButton.Yes and w.add_library_folder(folder):
            self.waiting = path
