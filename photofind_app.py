#!/usr/bin/env python3
"""Starts the Photo Finder desktop app (photofind/webapp). Kept at the top level so existing
shortcuts and the app bundle have one fixed file to run."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from photofind.webapp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
