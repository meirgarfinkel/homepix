import importlib
import os
import shutil
import tempfile

import numpy as np
import pytest
from PIL import Image

# A safety net under every test: nothing reaches the real data folder (~/.local/share/photofind), even
# a test that forgets the pf fixture or undoes its setting. Tests that need one get their own (pf).
_SAFE = tempfile.mkdtemp(prefix="photofind-tests-")
os.environ["PHOTOFIND_HOME"] = os.path.join(_SAFE, "data")
os.environ["XDG_DATA_HOME"] = os.path.join(_SAFE, "share")


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
