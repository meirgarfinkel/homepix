"""Opening a photo from outside: "Open With > HomePix" in the file manager, or a photo dropped on the
app's icon. The photo is shown large, and the arrows go through the other photos in its folder.

A photo that isn't in the library is shown just the same, straight from its folder: nothing is added
to the library. HomePix knows nothing else about it (faces, details), so it can only be looked at."""

import os

from PySide6.QtWidgets import QMessageBox

from ..core.db import open_db
from ..core.library import MEDIA_EXTS
from ..core.timeline import Item, load_items
from ..core.video import is_video
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
            from urllib.parse import urlparse
            from urllib.request import url2pathname

            a = url2pathname(urlparse(a).path)
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


def loose_items(path: str):
    """A photo the library doesn't have, and the photos and videos beside it, straight from the folder
    (by name, as a file manager lists them). They have no id: nothing about them is in the library."""
    folder = os.path.dirname(path)
    items = []
    with os.scandir(folder) as found:
        for e in found:
            if e.name.startswith(".") or os.path.splitext(e.name)[1].lower() not in MEDIA_EXTS:
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            if e.is_file():
                kind = "video" if is_video(e.path) else "photo"
                items.append(Item(None, e.path, "0000-00-00 00:00:00", f"{st.st_size}-{st.st_mtime_ns}", kind))
    items.sort(key=lambda it: os.path.basename(it.path).casefold())
    at = [i for i, it in enumerate(items) if it.path == path]
    return items, (at[0] if at else 0)


def items_for(path: str):
    """(items, index, in the library?) to show path among the others in its folder."""
    found = folder_items(path)
    return (*found, True) if found else (*loose_items(path), False)


class Opener:
    def __init__(self, window):
        self.window = window

    def open(self, paths):
        """Show the first of paths (more aren't opened at once: the arrows go through the folder)."""
        paths = given_files(paths)
        if not paths:
            return
        path = paths[0]
        self.window.bring_forward()
        workers.run(items_for, lambda res, err: self._found(path, res, err), path)

    def _found(self, path, res, err):
        w = self.window
        if err is not None:
            QMessageBox.warning(w, "HomePix", f"Couldn't open {os.path.basename(path)}:\n{err}")
            return
        items, index, known = res
        w.show_folder_in_viewer(os.path.dirname(path), items, index)
        if not known:
            w.statusBar().showMessage(
                f"{os.path.basename(path)} isn't in your library: view only.",
                10000,
            )
