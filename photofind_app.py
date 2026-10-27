#!/usr/bin/env python3
"""Starts Photo Finder. Kept at the top level so the installers and the app bundle have one fixed
file to run."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from photofind.ui.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
