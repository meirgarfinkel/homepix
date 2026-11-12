"""Every release says the same version everywhere it's written down."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from homepix import __version__

ROOT = Path(__file__).parent.parent


def test_the_installer_has_the_apps_version():
    iss = (ROOT / "packaging" / "homepix.iss").read_text()
    assert re.search(r'^#define AppVersion "(.+)"$', iss, re.M).group(1) == __version__


@pytest.mark.skipif(shutil.which("sed") is None, reason="needs sed, as the release workflows do")
def test_the_release_checks_read_the_versions_right():
    """Each `sed -n '...' file` the release workflows read a version with, run here: it must find it."""
    found = {}
    for wf, base in (("build.yml", ROOT), ("android.yml", ROOT / "android")):
        text = (ROOT / ".github" / "workflows" / wf).read_text()
        for expr, file in re.findall(r"sed -n '([^']+)' (\S+?)\)", text):
            out = subprocess.run(["sed", "-n", expr, str(base / file)], capture_output=True, text=True).stdout
            found[file] = out.strip()
    assert found["homepix/__init__.py"] == __version__ == found["packaging/homepix.iss"]
    assert re.fullmatch(r"\d+\.\d+", found["app/build.gradle.kts"])
