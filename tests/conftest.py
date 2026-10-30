import importlib
import os
import shutil
import struct
import tempfile
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

# A safety net under every test: nothing reaches the real data folder (~/.local/share/homepix), even
# a test that forgets the pf fixture or undoes its setting. Tests that need one get their own (pf).
_SAFE = tempfile.mkdtemp(prefix="homepix-tests-")
os.environ["HOMEPIX_HOME"] = os.path.join(_SAFE, "data")
os.environ["XDG_DATA_HOME"] = os.path.join(_SAFE, "share")
os.environ["HOMEPIX_RECYCLE_BIN"] = os.path.join(_SAFE, "recycle")  # (Windows) never the real Recycle Bin


class FakeClip:
    """Stands in for the search model: a vector from the photo's average colour."""

    def embed(self, img):
        v = np.resize(np.asarray(img, dtype=np.float32).mean(axis=(0, 1)), 512) + 1e-3
        return v / np.linalg.norm(v)


class FakeFaces:
    def find(self, img, path=None):
        return []


@pytest.fixture
def pf(tmp_path, monkeypatch):
    """homepix with its data folder in tmp_path and fake models (no downloads, fast)."""
    monkeypatch.setenv("HOMEPIX_HOME", str(tmp_path / "data"))
    library = importlib.import_module("homepix.core.library")
    monkeypatch.setattr(library, "ClipImage", FakeClip)
    monkeypatch.setattr(library, "Faces", FakeFaces)
    from homepix.core import config, db, indexer, media, people, search, settings, thumbs

    return SimpleNamespace(
        data_dir=config.data_dir,
        open_db=db.open_db,
        to_blob=db.to_blob,
        from_blob=db.from_blob,
        DatabaseVersionError=db.DatabaseVersionError,
        Indexer=indexer.Indexer,
        HEIF=media.HEIF,
        index_folder=library.index_folder,
        scan_folder=library.scan_folder,
        IGNORE_MARKER=library.IGNORE_MARKER,
        named_people=people.named_people,
        SearchIndex=search.SearchIndex,
        face_thumb=thumbs.face_thumb,
        add_folder=settings.add_folder,
        remove_folder=settings.remove_folder,
        set_excluded=settings.set_excluded,
        excluded_folders=settings.excluded_folders,
        library_folders=settings.library_folders,
    )


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Pictures"
    root.mkdir()
    return root


_n = 0


def photo(path, fmt="JPEG"):
    """Write a small photo with unique content (so its fingerprint is unique)."""
    global _n
    _n += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(_n)
    Image.fromarray(rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)).save(path, fmt)
    return path


def copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def rel(path, lib) -> str:
    """path relative to lib, with / between folders (as the tests write them on every system)."""
    return os.path.relpath(path, lib).replace(os.sep, "/")


def et_command() -> list[str]:
    """How to run ExifTool (the bundled one on Windows)."""
    from homepix.core import exiftool

    b = exiftool.bundled()
    if b and (b / "perl" / "so").is_dir():  # the Linux bundle's Perl needs its own libraries
        return ["env", f"LD_LIBRARY_PATH={b / 'perl' / 'so'}", *exiftool.command()]
    return exiftool.command()


def rows(pf, lib):
    """{path relative to lib: row} for everything indexed."""
    db = pf.open_db()
    try:
        return {rel(r["path"], lib): dict(r) for r in db.execute("SELECT * FROM photos")}
    finally:
        db.close()


# --- the Trash: the freedesktop one, or on Windows the Recycle Bin --------------------------------------


def own_trash(monkeypatch, tmp_path):
    """A Trash (or Recycle Bin) of the test's own, in tmp_path."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOMEPIX_RECYCLE_BIN", str(tmp_path / "recycle"))


def trashed_files(tmp_path) -> list[Path]:
    """The files in the test's Trash."""
    if os.name == "nt":
        folder = tmp_path / "recycle"
        return sorted(folder.glob("$R*")) if folder.exists() else []
    folder = tmp_path / "xdg" / "Trash" / "files"
    return sorted(folder.iterdir()) if folder.exists() else []


def recycled_from(info) -> str:
    """Where a Recycle Bin item ($I file) came from."""
    data = Path(info).read_bytes()
    n = struct.unpack_from("<i", data, 24)[0]
    return data[28 : 28 + 2 * n].decode("utf-16-le").rstrip("\0")


def trash_records(tmp_path) -> list[str]:
    """Where each item the Trash knows of came from (its info files)."""
    if os.name == "nt":
        folder = tmp_path / "recycle"
        return sorted(recycled_from(i) for i in folder.glob("$I*")) if folder.exists() else []
    folder = tmp_path / "xdg" / "Trash" / "info"
    found = []
    for i in sorted(folder.iterdir()) if folder.exists() else []:
        for line in i.read_text().splitlines():
            if line.startswith("Path="):
                found.append(urllib.parse.unquote(line[5:]))
    return sorted(found)


@pytest.fixture
def home(pf, lib, tmp_path, monkeypatch):
    """lib as the library, with a Trash of its own. Yields (pf, an open database)."""
    own_trash(monkeypatch, tmp_path)
    pf.add_folder(lib)
    db = pf.open_db()
    yield pf, db
    db.close()


# --- the app ---------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_until(app, cond, timeout=20.0):
    """Let the app run until cond() is true (fails the test if it isn't within timeout seconds)."""
    end = time.time() + timeout
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert cond()


def open_window(app, ready=lambda w: w.loaded, index=False, size=(1000, 700)):
    """The main window, shown, once ready(window) is true (ready=None: at once)."""
    from homepix.ui.main_window import MainWindow

    w = MainWindow(index=index)
    w.resize(*size)
    w.show()
    if ready is not None:
        wait_until(app, lambda: ready(w))
    return w
