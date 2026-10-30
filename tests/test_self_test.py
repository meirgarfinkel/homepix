"""The app's own --self-test, which every build runs, run here with the other tests too: something it
checks that breaks shows up in seconds, not after a build."""

import os
import subprocess
import sys
from pathlib import Path


def test_the_apps_self_test_passes(tmp_path):
    env = os.environ | {"HOMEPIX_HOME": str(tmp_path / "data"), "QT_QPA_PLATFORM": "offscreen"}
    run = subprocess.run(
        [sys.executable, "-m", "homepix.ui", "--self-test"],
        cwd=Path(__file__).parent.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode == 0 and "SELF-TEST OK" in run.stdout, run.stdout + run.stderr
