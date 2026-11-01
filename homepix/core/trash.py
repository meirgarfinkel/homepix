r"""The desktop's Trash (the freedesktop.org standard that GNOME Files, Dolphin, Nemo and Thunar use),
so a photo HomePix deletes shows up there and can be restored from either.

A file in the home Trash goes to ~/.local/share/Trash; one on another drive goes to that drive's
own Trash (.Trash/<uid> or .Trash-<uid> at its top), so it's never copied across drives.

On Windows it's the Recycle Bin, the same way: each drive has one (X:\$Recycle.Bin\<user's SID>), where
a deleted file is renamed to $R<tag>.<ext> beside a small $I<tag>.<ext> saying where it came from, so
Explorer can show and restore it too.

On a Mac it's the Finder's Trash, through macOS itself (so the Finder's Put Back works). macOS picks
the name in the Trash as the file goes, so a reserved name there is only a guess until then.
"""

import datetime as dt
import functools
import os
import secrets
import stat
import string
import struct
import sys
import time
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


def _with_subfolders(trash: Path) -> Path:
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
            return _with_subfolders(home), None
    except OSError:
        pass
    top = _mount_point(folder)
    uid = str(os.getuid())
    shared = top / ".Trash"
    try:  # an admin-made .Trash: a real folder, sticky, not a link
        st = shared.lstat()
        if stat.S_ISDIR(st.st_mode) and st.st_mode & stat.S_ISVTX:
            return _with_subfolders(shared / uid), top
    except OSError:
        pass
    try:
        return _with_subfolders(top / f".Trash-{uid}"), top
    except OSError as e:
        raise TrashError(f"There's no Trash on the drive {top} that HomePix can use: {e}") from e


def reserve(path) -> str:
    """Choose the file's name in the Trash and reserve it by making its info file (exclusively, so
    two deletions never pick the same name). Returns the path it's to be moved to."""
    path = Path(os.path.abspath(path))
    if mac():
        return str(Path.home() / ".Trash" / path.name)
    if os.name == "nt":
        return _reserve_recycled(path)
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
        f.write(
            "[Trash Info]\n"
            f"Path={urllib.parse.quote(shown)}\n"
            f"DeletionDate={dt.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n"
        )
    return str(folder / "files" / name)


def unreserve(trashed):
    """Remove a trashed file's info file (after restoring it, or if trashing it failed)."""
    t = Path(trashed)
    if mac():
        return
    if os.name == "nt":
        (t.parent / f"$I{t.name[2:]}").unlink(missing_ok=True)
    else:
        (t.parent.parent / "info" / f"{t.name}.trashinfo").unlink(missing_ok=True)


def send(path, reserved) -> str:
    """Move a file to the Trash, at the name reserve() gave it. Returns where it went."""
    if mac():
        return _mac_trash(os.fspath(path))
    rename_noreplace(path, reserved)
    return os.fspath(reserved)


def restore(trashed, dst):
    """Put a file back from the Trash at dst (which must not exist)."""
    rename_noreplace(trashed, dst)
    unreserve(trashed)


# --- Windows: the Recycle Bin ------------------------------------------------------------------------

_TAG = string.ascii_uppercase + string.digits
_FILETIME_1970 = 116444736000000000  # 1970-01-01 in Windows time (100 ns steps since 1601)


@functools.cache
def _sid() -> str:
    """The signed-in user's security ID, which names their folder in each Recycle Bin."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]

    def check(ok):
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    token = wintypes.HANDLE()
    check(advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)))  # TOKEN_QUERY
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # 1: TokenUser; asks the size
        buf = ctypes.create_string_buffer(size.value)
        check(advapi32.GetTokenInformation(token, 1, buf, size, ctypes.byref(size)))
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]  # TOKEN_USER starts with the SID's address
        text = wintypes.LPWSTR()
        check(advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)))
        try:
            return text.value
        finally:
            kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel32.CloseHandle(token)


def recycle_bin(path: Path) -> Path:
    """The Recycle Bin folder for a file (HOMEPIX_RECYCLE_BIN instead, for tests)."""
    if os.environ.get("HOMEPIX_RECYCLE_BIN"):
        folder = Path(os.environ["HOMEPIX_RECYCLE_BIN"])
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    drive = os.path.splitdrive(str(path))[0]
    if not drive or drive.startswith(("\\\\", "//")):
        raise TrashError("Files in a network folder can't go to the Recycle Bin")
    top = Path(drive + "\\") / "$Recycle.Bin"
    if not top.is_dir():  # Windows keeps none on removable drives
        raise TrashError(f"The drive {drive} has no Recycle Bin")
    folder = top / _sid()
    try:
        folder.mkdir(exist_ok=True)
    except OSError as e:
        raise TrashError(f"HomePix can't use the Recycle Bin on {drive}: {e}") from e
    return folder


def _reserve_recycled(path: Path) -> str:
    folder = recycle_bin(path)
    ext = path.suffix
    while True:
        tag = "".join(secrets.choice(_TAG) for _ in range(6))
        info = folder / f"$I{tag}{ext}"
        try:
            fd = os.open(info, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            continue
        if not os.path.lexists(folder / f"$R{tag}{ext}"):
            break
        os.close(fd)
        info.unlink()
    original = str(path) + "\0"
    when = int(time.time() * 10_000_000) + _FILETIME_1970
    with os.fdopen(fd, "wb") as f:  # version 2 (Windows 10 and later): size, date, then the path
        f.write(struct.pack("<qqqi", 2, path.stat().st_size, when, len(original)) + original.encode("utf-16-le"))
    return str(folder / f"$R{tag}{ext}")


# --- Mac: the Finder's Trash -------------------------------------------------------------------------


def mac() -> bool:
    """On a Mac (tests there use the freedesktop Trash in a folder of their own instead)."""
    return sys.platform == "darwin" and not os.environ.get("HOMEPIX_FREEDESKTOP_TRASH")


def _mac_trash(path: str) -> str:
    """[NSFileManager trashItemAtURL:resultingItemURL:error:], called through the Objective-C runtime."""
    import ctypes
    import ctypes.util

    objc = ctypes.CDLL(ctypes.util.find_library("objc"))
    ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
    objc.objc_getClass.restype = objc.sel_registerName.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
    objc.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
    address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value

    def send(restype, obj, selector, argtypes=(), *args):  # each call with its exact types (Apple Silicon)
        fn = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(address)
        return fn(obj, objc.sel_registerName(selector), *args)

    def text(ns) -> str:
        return send(ctypes.c_char_p, ns, b"UTF8String").decode() if ns else ""

    ID, OUT = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)

    pool = objc.objc_autoreleasePoolPush()
    try:
        cls = objc.objc_getClass
        name = send(ID, cls(b"NSString"), b"stringWithUTF8String:", [ctypes.c_char_p], path.encode())
        url = send(ID, cls(b"NSURL"), b"fileURLWithPath:", [ID], name)
        manager = send(ID, cls(b"NSFileManager"), b"defaultManager")
        went, err = ctypes.c_void_p(), ctypes.c_void_p()
        selector = b"trashItemAtURL:resultingItemURL:error:"
        ok = send(ctypes.c_bool, manager, selector, [ID, OUT, OUT], url, ctypes.byref(went), ctypes.byref(err))
        if not ok:
            why = text(send(ID, err, b"localizedDescription")) if err.value else ""
            raise TrashError(why or "macOS couldn't move it to the Trash")
        return text(send(ID, went, b"path")) if went.value else str(Path.home() / ".Trash")
    finally:
        objc.objc_autoreleasePoolPop(pool)
