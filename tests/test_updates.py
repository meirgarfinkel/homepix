"""Install updates: the check, the download, and installing the way this copy was installed."""

import hashlib
import os
import stat
import subprocess

import pytest
from conftest import open_window, wait_until

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from homepix.core import updates  # noqa: E402


def release(tmp_path, tag, name, data=b"new HomePix"):
    """A release as GitHub describes it, its file served from disk."""
    f = tmp_path / "served" / name
    f.parent.mkdir(exist_ok=True)
    f.write_bytes(data)
    asset = {
        "name": name,
        "browser_download_url": f.as_uri(),
        "size": len(data),
        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
    }
    return {"tag_name": tag, "assets": [asset]}


def test_only_a_higher_version_is_newer():
    assert updates.newer({"tag_name": "v1.0.1"}, "1.0.0")
    assert updates.newer({"tag_name": "v1.10.0"}, "1.9.9")
    assert not updates.newer({"tag_name": "v1.0.0"}, "1.0.0")
    assert not updates.newer({"tag_name": "v0.9.0"}, "1.0.0")
    assert not updates.newer({"tag_name": "nightly"}, "1.0.0")


def test_the_file_for_each_kind_of_install(tmp_path):
    rel = release(tmp_path, "v2.0.0", "HomePix.deb")
    assert updates.file_for(rel, "deb")["name"] == "HomePix.deb"
    assert updates.file_for(rel, "windows") is None


def test_a_download_is_checked(tmp_path):
    rel = release(tmp_path, "v2.0.0", "HomePix.deb", b"x" * 3000)
    seen = []
    assert updates.download(rel["assets"][0], tmp_path / "got.deb", lambda d, t: seen.append((d, t)))
    assert (tmp_path / "got.deb").read_bytes() == b"x" * 3000 and seen[-1] == (3000, 3000)
    rel["assets"][0]["digest"] = "sha256:" + "0" * 64  # not what came
    with pytest.raises(OSError, match="damaged"):
        updates.download(rel["assets"][0], tmp_path / "bad.deb")
    assert not (tmp_path / "bad.deb").exists() and not (tmp_path / "bad.deb.part").exists()


@pytest.fixture
def window(pf, app):
    w = open_window(app, ready=None, size=(900, 600))
    yield w
    w.close()


@pytest.fixture
def said(monkeypatch):
    """What the message boxes said; questions are answered No."""
    said = []
    box = QtWidgets.QMessageBox

    def info(parent, title, text, *a, **k):
        said.append(text)
        return box.StandardButton.Ok

    def ask(parent, title, text, *a, **k):
        said.append(text)
        return box.StandardButton.No

    monkeypatch.setattr(box, "information", staticmethod(info))
    monkeypatch.setattr(box, "warning", staticmethod(info))
    monkeypatch.setattr(box, "question", staticmethod(ask))
    return said


def test_the_latest_version_says_so(window, app, said, monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "how_installed", lambda: "appimage")
    monkeypatch.setattr(updates, "latest_release", lambda: release(tmp_path, "v1.0.0", "HomePix-x86_64.AppImage"))
    window.updater.check()
    wait_until(app, lambda: said)
    assert said == ["This is the latest version."] and not window.updater.busy


def test_an_appimage_replaces_itself_with_the_new_one(window, app, said, monkeypatch, tmp_path):
    old = tmp_path / "apps" / "HomePix-x86_64.AppImage"
    old.parent.mkdir()
    old.write_bytes(b"old HomePix")
    monkeypatch.setenv("APPIMAGE", str(old))
    monkeypatch.setattr(updates, "how_installed", lambda: "appimage")
    monkeypatch.setattr(updates, "latest_release", lambda: release(tmp_path, "v9.0.0", "HomePix-x86_64.AppImage"))
    window.updater.check()
    wait_until(app, lambda: said)
    assert said == ["HomePix 9.0.0 is installed. Restart now?"]
    assert old.read_bytes() == b"new HomePix" and os.stat(old).st_mode & stat.S_IXUSR


def test_a_deb_is_installed_with_the_password_prompt(window, app, said, monkeypatch, tmp_path):
    from homepix.ui import updates as ui_updates

    ran = []
    monkeypatch.setattr(updates, "how_installed", lambda: "deb")
    monkeypatch.setattr(updates, "latest_release", lambda: release(tmp_path, "v9.0.0", "HomePix.deb"))
    monkeypatch.setattr(ui_updates.shutil, "which", lambda name: "/usr/bin/pkexec")
    monkeypatch.setattr(
        ui_updates.subprocess,
        "run",
        lambda cmd, **k: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    window.updater.check()
    wait_until(app, lambda: said)
    assert ran[0][:4] == ["/usr/bin/pkexec", "apt-get", "install", "-y"] and ran[0][4].endswith("HomePix.deb")
    assert said == ["HomePix 9.0.0 is installed. Restart now?"]
