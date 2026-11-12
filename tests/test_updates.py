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


@pytest.fixture(autouse=True)
def served_here(tmp_path, monkeypatch):
    """Releases' files served from disk, as if from HomePix's own GitHub releases."""
    monkeypatch.setattr(updates, "DOWNLOADS", (tmp_path / "served").as_uri() + "/")


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


def test_the_newest_desktop_release_whatever_github_calls_latest():
    def rel(tag, name="HomePix.deb", **k):
        return {"tag_name": tag, "assets": [{"name": name}], **k}

    releases = [
        rel("android-v0.9", "HomePix-phone.apk"),  # (a phone release marked latest by mistake)
        rel("v9.0.0", prerelease=True),
        rel("v8.0.0", draft=True),
        rel("v1.9.0"),
        rel("v1.10.0"),
        rel("v1.11.0", "HomePix.dmg"),  # (no .deb in it: the Linux build failed, say)
        "junk",
    ]
    assert updates.newest(releases)["tag_name"] == "v1.11.0"
    assert updates.newest(releases, "deb")["tag_name"] == "v1.10.0"
    assert updates.newest([rel("android-v0.9")], "deb") == {}


def test_an_update_without_a_checksum_or_from_elsewhere_is_refused(tmp_path, monkeypatch):
    rel = release(tmp_path, "v2.0.0", "HomePix.deb")
    asset = rel["assets"][0]
    with pytest.raises(OSError, match="no checksum"):
        updates.download({k: v for k, v in asset.items() if k != "digest"}, tmp_path / "got.deb")
    monkeypatch.setattr(updates, "DOWNLOADS", "https://github.com/meirgarfinkel/homepix/releases/download/")
    with pytest.raises(OSError, match="own releases"):
        updates.download(asset, tmp_path / "got.deb")  # (a file:// address)
    with pytest.raises(OSError, match="own releases"):
        updates.download(asset | {"browser_download_url": "https://example.com/HomePix.deb"}, tmp_path / "got.deb")
    assert not (tmp_path / "got.deb").exists()


def test_a_download_that_checks_out_is_kept(tmp_path):
    rel = release(tmp_path, "v2.0.0", "HomePix.deb", b"y" * 5000)
    asset = rel["assets"][0]
    assert updates.download(asset, tmp_path / "got.deb")
    (tmp_path / "served" / "HomePix.deb").unlink()  # (not fetched again: it's here)
    assert updates.download(asset, tmp_path / "got.deb")
    (tmp_path / "got.deb").write_bytes(b"z" * 5000)  # damaged since: fetched again, and fails here
    with pytest.raises(OSError):
        updates.download(asset, tmp_path / "got.deb")


@pytest.mark.skipif(os.name == "nt", reason="the new copy is started by sh on Linux and Mac")
def test_the_new_copy_starts_only_once_this_one_has_gone(tmp_path):
    import sys
    import time

    from homepix.ui import updates as ui_updates

    marker = tmp_path / "started"
    new = tmp_path / "new-homepix"
    new.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    new.chmod(0o755)
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    waiter = subprocess.Popen(["sh", "-c", ui_updates._WHEN_GONE + 'exec "$0"', str(new), str(old.pid)])
    time.sleep(0.8)
    assert not marker.exists()  # (the old one is still running)
    old.wait()
    waiter.wait(timeout=10)
    assert marker.exists()


def test_a_mac_updates_from_the_disk_image(tmp_path):
    rel = release(tmp_path, "v2.0.0", "HomePix.dmg")
    assert updates.file_for(rel, "mac")["name"] == "HomePix.dmg"


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
    monkeypatch.setattr(updates, "latest_release", lambda *_: release(tmp_path, "v1.0.0", "HomePix-x86_64.AppImage"))
    window.updater.check()
    wait_until(app, lambda: said)
    assert said == ["This is the latest version."] and not window.updater.busy


def test_an_appimage_replaces_itself_with_the_new_one(window, app, said, monkeypatch, tmp_path):
    old = tmp_path / "apps" / "HomePix-x86_64.AppImage"
    old.parent.mkdir()
    old.write_bytes(b"old HomePix")
    monkeypatch.setenv("APPIMAGE", str(old))
    monkeypatch.setattr(updates, "how_installed", lambda: "appimage")
    monkeypatch.setattr(updates, "latest_release", lambda *_: release(tmp_path, "v9.0.0", "HomePix-x86_64.AppImage"))
    window.updater.check()
    wait_until(app, lambda: said)
    assert said == ["HomePix 9.0.0 is installed. Restart now?"]
    assert old.read_bytes() == b"new HomePix"
    if os.name != "nt":  # Windows files have no run permission
        assert os.stat(old).st_mode & stat.S_IXUSR


def test_a_deb_is_installed_with_the_password_prompt(window, app, said, monkeypatch, tmp_path):
    from homepix.ui import updates as ui_updates

    ran = []
    monkeypatch.setattr(updates, "how_installed", lambda: "deb")
    monkeypatch.setattr(updates, "latest_release", lambda *_: release(tmp_path, "v9.0.0", "HomePix.deb"))
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


def test_a_mac_app_is_swapped_for_the_one_in_the_disk_image(window, app, said, monkeypatch, tmp_path):
    import shutil

    from homepix.ui import updates as ui_updates

    here = tmp_path / "Applications" / "HomePix.app"
    (here / "Contents").mkdir(parents=True)
    (here / "Contents" / "version").write_text("old")
    ran = []

    def run(cmd, **k):  # hdiutil and ditto, as a Mac does them
        ran.append(cmd[:2])
        if cmd[:2] == ["hdiutil", "attach"]:
            inside = ui_updates.Path(cmd[cmd.index("-mountpoint") + 1]) / "HomePix.app" / "Contents"
            inside.mkdir(parents=True)
            (inside / "version").write_text("new")
        elif cmd[0] == "ditto":
            shutil.copytree(cmd[1], cmd[2])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ui_updates, "mac_bundle", lambda: here)
    monkeypatch.setattr(ui_updates.subprocess, "run", run)
    monkeypatch.setattr(updates, "how_installed", lambda: "mac")
    monkeypatch.setattr(updates, "latest_release", lambda *_: release(tmp_path, "v9.0.0", "HomePix.dmg"))
    window.updater.check()
    wait_until(app, lambda: said)
    assert said == ["HomePix 9.0.0 is installed. Restart now?"]
    assert (here / "Contents" / "version").read_text() == "new"
    assert sorted(os.listdir(here.parent)) == ["HomePix.app"]  # nothing left beside it
    assert ran == [["hdiutil", "attach"], ["ditto", ran[1][1]], ["hdiutil", "detach"]]


def test_a_mac_app_opened_from_the_disk_image_asks_to_be_moved_first(window, app, said, monkeypatch, tmp_path):
    from homepix.ui import updates as ui_updates

    monkeypatch.setattr(ui_updates, "mac_bundle", lambda: ui_updates.Path("/Volumes/HomePix/HomePix.app"))
    monkeypatch.setattr(updates, "how_installed", lambda: "mac")
    monkeypatch.setattr(updates, "latest_release", lambda *_: release(tmp_path, "v9.0.0", "HomePix.dmg"))
    window.updater.check()
    wait_until(app, lambda: said)
    assert "Applications folder first" in said[0]


def test_at_startup_a_newer_version_is_downloaded_then_offered(window, app, said, monkeypatch, tmp_path):
    from homepix.ui import updates as ui_updates

    old = tmp_path / "apps" / "HomePix-x86_64.AppImage"
    old.parent.mkdir()
    old.write_bytes(b"old HomePix")
    monkeypatch.setenv("APPIMAGE", str(old))
    monkeypatch.setattr(updates, "how_installed", lambda: "appimage")
    monkeypatch.setattr(updates, "latest_release", lambda *_: release(tmp_path, "v9.0.0", "HomePix-x86_64.AppImage"))
    restarted = []
    monkeypatch.setattr(ui_updates, "restart", lambda w: restarted.append(w))
    bar = window.update_bar
    window.updater.check_quietly()
    wait_until(app, lambda: not bar.isHidden())
    assert bar.label.text() == "HomePix 9.0.0 is ready." and old.read_bytes() == b"old HomePix"
    bar.button.click()  # Restart to update: installed, restarted, nothing asked
    wait_until(app, lambda: restarted)
    assert old.read_bytes() == b"new HomePix" and bar.isHidden() and said == []


def test_a_failed_install_from_the_bar_offers_it_again(window, app, said, monkeypatch, tmp_path):
    from homepix.ui import updates as ui_updates

    monkeypatch.setenv("APPIMAGE", str(tmp_path / "nowhere" / "HomePix-x86_64.AppImage"))
    monkeypatch.setattr(updates, "how_installed", lambda: "appimage")
    monkeypatch.setattr(updates, "latest_release", lambda *_: release(tmp_path, "v9.0.0", "HomePix-x86_64.AppImage"))
    monkeypatch.setattr(ui_updates, "_replace_appimage", lambda new: (_ for _ in ()).throw(OSError("disk full")))
    bar = window.update_bar
    window.updater.check_quietly()
    wait_until(app, lambda: not bar.isHidden())
    bar.button.click()
    wait_until(app, lambda: said)
    assert "disk full" in said[0] and not bar.isHidden() and not window.updater.busy


def test_nothing_shows_at_startup_without_an_update(window, app, said, monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "how_installed", lambda: "appimage")
    monkeypatch.setattr(updates, "latest_release", lambda *_: release(tmp_path, "v1.0.0", "HomePix-x86_64.AppImage"))
    window.updater.check_quietly()
    wait_until(app, lambda: not window.updater.busy)
    assert window.update_bar.isHidden() and said == []


def test_windows_updates_quietly_and_reopens(window, app, said, monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(os, "startfile", lambda path, arguments="": ran.append((path, arguments)), raising=False)
    monkeypatch.setattr(updates, "how_installed", lambda: "windows")
    monkeypatch.setattr(updates, "latest_release", lambda *_: release(tmp_path, "v9.0.0", "HomePixSetup.exe"))
    window.updater.check()
    wait_until(app, lambda: ran)
    path, args = ran[0]
    assert path.endswith("HomePixSetup.exe") and "/SILENT" in args and "/relaunch=1" in args


def test_the_installer_reopens_homepix_after_a_quiet_update():
    from pathlib import Path

    from homepix.ui.updates import WINDOWS_QUIET

    iss = (Path(__file__).parent.parent / "packaging" / "homepix.iss").read_text()
    assert "/relaunch=1" in WINDOWS_QUIET and "{param:relaunch|0}" in iss and "Check: Relaunch" in iss


def test_an_installer_that_wont_start_says_so(window, app, said, monkeypatch, tmp_path):
    def fail(path, arguments=""):
        raise OSError("blocked")

    monkeypatch.setattr(os, "startfile", fail, raising=False)
    monkeypatch.setattr(updates, "how_installed", lambda: "windows")
    monkeypatch.setattr(updates, "latest_release", lambda *_: release(tmp_path, "v9.0.0", "HomePixSetup.exe"))
    window.updater.check()
    wait_until(app, lambda: said)
    assert "blocked" in said[0] and not window.updater.busy
