"""What's different on a Mac, checked on every system (the Mac test machine runs everything else)."""

import os
import sys

from homepix.core import config, exiftool, trash


def test_a_mac_keeps_its_data_in_application_support(monkeypatch, tmp_path):
    monkeypatch.delenv("HOMEPIX_HOME")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    assert config.data_dir() == tmp_path / "Library" / "Application Support" / "homepix"


def test_a_macs_exiftool_runs_on_the_macs_own_perl(monkeypatch, tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "exiftool").write_text("#!/usr/bin/perl\n")
    monkeypatch.delenv("HOMEPIX_EXIFTOOL", raising=False)
    monkeypatch.setenv("HOMEPIX_EXIFTOOL_DIR", str(tmp_path))
    assert exiftool.command() == ["/usr/bin/perl", str(tmp_path / "exiftool")]


def test_on_a_mac_the_system_names_the_file_in_the_trash(monkeypatch, tmp_path):
    monkeypatch.delenv("HOMEPIX_FREEDESKTOP_TRASH")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(trash, "_mac_trash", lambda path: f"/Users/me/.Trash/{os.path.basename(path)} 2")
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    guess = trash.reserve(f)
    assert guess.endswith("a.jpg")
    assert trash.send(f, guess) == "/Users/me/.Trash/a.jpg 2"
    trash.unreserve(guess)  # nothing to let go of: macOS keeps its own records
