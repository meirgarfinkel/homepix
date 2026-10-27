"""The desktop's Trash (the freedesktop.org standard that GNOME Files, Dolphin, Nemo and Thunar use),
so a photo Photo Finder deletes shows up there and can be restored from either.

A file in the home Trash goes to ~/.local/share/Trash; one on another drive goes to that drive's
own Trash (.Trash/<uid> or .Trash-<uid> at its top), so it's never copied across drives.
"""

import datetime as dt
import os
import stat
import urllib.parse
from pathlib import Path

from .safefs import rename_noreplace


class TrashError(OSError):
    pass


def _home_trash() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "Trash"


def _mount_point(path: Path) -> Path:
    path = path.resolve()
    dev = path.stat().st_dev
    while path.parent != path and path.parent.stat().st_dev == dev:
        path = path.parent
    return path


def _ready(trash: Path) -> Path:
    for sub in ("files", "info"):
        (trash / sub).mkdir(parents=True, exist_ok=True, mode=0o700)
    return trash


def trash_for(path: Path) -> tuple[Path, Path | None]:
    """(the Trash folder for path, the drive's top folder if it's a drive's own Trash, else None)."""
    home = _home_trash()
    folder = path.parent
    try:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if home.stat().st_dev == folder.stat().st_dev:
            return _ready(home), None
    except OSError:
        pass
    if os.name == "nt":
        raise TrashError("The Recycle Bin isn't supported yet")
    top = _mount_point(folder)
    uid = str(os.getuid())
    shared = top / ".Trash"
    try:  # an admin-made .Trash: a real folder, sticky, not a link
        st = shared.lstat()
        if stat.S_ISDIR(st.st_mode) and st.st_mode & stat.S_ISVTX:
            return _ready(shared / uid), top
    except OSError:
        pass
    try:
        return _ready(top / f".Trash-{uid}"), top
    except OSError as e:
        raise TrashError(f"There's no Trash on the drive {top} that Photo Finder can use: {e}") from e


def reserve(path) -> str:
    """Choose the file's name in the Trash and reserve it by making its info file (exclusively, so
    two deletions never pick the same name). Returns the path it's to be moved to."""
    path = Path(os.path.abspath(path))
    folder, top = trash_for(path)
    name, n = path.name, 1
    while True:
        info = folder / "info" / f"{name}.trashinfo"
        try:
            fd = os.open(info, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            n += 1
            name = f"{path.stem}.{n}{path.suffix}"
    shown = os.path.relpath(path, top) if top else str(path)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("[Trash Info]\n"
                f"Path={urllib.parse.quote(shown)}\n"
                f"DeletionDate={dt.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n")
    return str(folder / "files" / name)


def unreserve(trashed):
    """Remove a trashed file's info file (after restoring it, or if trashing it failed)."""
    t = Path(trashed)
    (t.parent.parent / "info" / f"{t.name}.trashinfo").unlink(missing_ok=True)


def trash(path) -> str:
    """Move a file to the Trash. Returns where it is now (to restore it from)."""
    dest = reserve(path)
    try:
        rename_noreplace(path, dest)
    except BaseException:
        unreserve(dest)
        raise
    return dest


def restore(trashed, dst):
    """Put a file back from the Trash at dst (which must not exist)."""
    rename_noreplace(trashed, dst)
    unreserve(trashed)
