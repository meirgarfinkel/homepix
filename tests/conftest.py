import importlib
import os
import shutil
import tempfile
import urllib.parse
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

# A safety net under every test: nothing reaches the real data folder (~/.local/share/photofind), even
# a test that forgets the pf fixture or undoes its setting. Tests that need one get their own (pf).
_SAFE = tempfile.mkdtemp(prefix="photofind-tests-")
os.environ["PHOTOFIND_HOME"] = os.path.join(_SAFE, "data")
os.environ["XDG_DATA_HOME"] = os.path.join(_SAFE, "share")
os.environ["PHOTOFIND_RECYCLE_BIN"] = os.path.join(_SAFE, "recycle")  # (Windows) never the real Recycle Bin


class FakeClip:
    """Stands in for the search model: a vector from the photo's average colour."""

    def embed(self, img):
        v = np.resize(np.asarray(img, dtype=np.float32).mean(axis=(0, 1)), 512) + 1e-3
        return v / np.linalg.norm(v)


class FakeFaces:
    def find(self, img):
        return []


@pytest.fixture
def pf(tmp_path, monkeypatch):
    """photofind with its data folder in tmp_path and fake models (no downloads, fast)."""
    monkeypatch.setenv("PHOTOFIND_HOME", str(tmp_path / "data"))
    library = importlib.import_module("photofind.core.library")
    monkeypatch.setattr(library, "ClipImage", FakeClip)
    monkeypatch.setattr(library, "Faces", FakeFaces)
    return importlib.import_module("photofind.core")


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


def rows(pf, lib):
    """{path relative to lib: row} for everything indexed."""
    db = pf.open_db()
    try:
        return {r["path"][len(str(lib)) + 1:]: dict(r) for r in db.execute("SELECT * FROM photos")}
    finally:
        db.close()


# --- the Trash: the freedesktop one, or on Windows the Recycle Bin --------------------------------------

def own_trash(monkeypatch, tmp_path):
    """A Trash (or Recycle Bin) of the test's own, in tmp_path."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("PHOTOFIND_RECYCLE_BIN", str(tmp_path / "recycle"))


def trashed_files(tmp_path) -> list[Path]:
    """The files in the test's Trash."""
    if os.name == "nt":
        folder = tmp_path / "recycle"
        return sorted(folder.glob("$R*")) if folder.exists() else []
    folder = tmp_path / "xdg" / "Trash" / "files"
    return sorted(folder.iterdir()) if folder.exists() else []


def trash_records(tmp_path) -> list[str]:
    """Where each item the Trash knows of came from (its info files)."""
    if os.name == "nt":
        from photofind.core.trash import recycled_from

        folder = tmp_path / "recycle"
        return sorted(recycled_from(i) for i in folder.glob("$I*")) if folder.exists() else []
    folder = tmp_path / "xdg" / "Trash" / "info"
    found = []
    for i in sorted(folder.iterdir()) if folder.exists() else []:
        for line in i.read_text().splitlines():
            if line.startswith("Path="):
                found.append(urllib.parse.unquote(line[5:]))
    return sorted(found)
