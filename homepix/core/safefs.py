"""Moving and copying files so that an existing file is never overwritten.

A plain rename silently replaces whatever is at the destination (that's how the Sep 2026 pipeline
lost 511 photos). Here every rename refuses to: with renameat2(RENAME_NOREPLACE) on Linux and
renamex_np(RENAME_EXCL) on a Mac, which the system does in one step; where a filesystem doesn't
support that, by hard-linking (which also fails on an existing name) and, as a last resort (some USB
drives' filesystems), by checking first.
Copies go to a hidden partial file, are flushed to disk, and only then take their real name.
"""

import ctypes
import errno
import os
import shutil
import sys

_RENAME_NOREPLACE = 1  # Linux
_RENAME_EXCL = 4  # Mac
_AT_FDCWD = -100
_libc = None
try:
    if sys.platform.startswith("linux"):
        _libc = ctypes.CDLL(None, use_errno=True)
        _libc.renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    elif sys.platform == "darwin":
        _libc = ctypes.CDLL(None, use_errno=True)
        _libc.renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
except (OSError, AttributeError):
    _libc = None


def _rename_once(src: bytes, dst: bytes) -> int:
    """The system's own no-replace rename: 0, or the error number."""
    if sys.platform == "darwin":
        ok = _libc.renamex_np(src, dst, _RENAME_EXCL) == 0
    else:
        ok = _libc.renameat2(_AT_FDCWD, src, _AT_FDCWD, dst, _RENAME_NOREPLACE) == 0
    return 0 if ok else ctypes.get_errno()


def rename_noreplace(src, dst):
    """Rename src to dst on the same filesystem. Raises FileExistsError if dst exists, and OSError
    with errno EXDEV if they're on different filesystems."""
    src, dst = os.fspath(src), os.fspath(dst)
    if os.name == "nt":
        os.rename(src, dst)  # Windows never replaces an existing file here
        return
    if _libc is not None:
        e = _rename_once(os.fsencode(src), os.fsencode(dst))
        if e == 0:
            return
        if e == errno.EEXIST:
            raise FileExistsError(e, os.strerror(e), dst)
        if e not in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise OSError(e, os.strerror(e), src)
    try:  # the filesystem can't do it in one step: a hard link also refuses an existing name
        os.link(src, dst)
    except FileExistsError:
        raise
    except OSError as e:
        if e.errno == errno.EXDEV:
            raise
        if os.path.lexists(dst):  # no hard links here either (FAT, exFAT): check, then rename
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), dst) from None
        os.rename(src, dst)
        return
    os.unlink(src)


def _copy_to_partial(src, partial):
    """Copy src's bytes and dates into a new file (never an existing one), flushed to disk."""
    with open(src, "rb") as fin, open(partial, "xb") as fout:
        shutil.copyfileobj(fin, fout, 1024 * 1024)
        fout.flush()
        os.fsync(fout.fileno())
    shutil.copystat(src, partial)


def copy_file(src, dst, partial):
    """Copy src to dst (which must not exist) by way of the hidden file partial."""
    _copy_to_partial(src, partial)
    try:
        rename_noreplace(partial, dst)
    except BaseException:
        os.unlink(partial)
        raise


def move_file(src, dst, partial):
    """Move src to dst (which must not exist). Across filesystems: copy by way of partial, check the
    size, then remove src."""
    try:
        rename_noreplace(src, dst)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    copy_file(src, dst, partial)
    if os.path.getsize(dst) != os.path.getsize(src):
        raise OSError(errno.EIO, "the copy came out a different size; the original is kept", src)
    os.unlink(src)


def free_name(folder, name: str, taken=()) -> str:
    """name if nothing in folder has it (and it isn't in taken), else name_1, name_2, ... the way the
    library names photos taken in the same second."""
    stem, ext = os.path.splitext(name)
    candidate, n = name, 0
    while candidate in taken or os.path.lexists(os.path.join(folder, candidate)):
        n += 1
        candidate = f"{stem}_{n}{ext}"
    return candidate
