"""Settings you might want to tune, and where Photo Finder keeps its data."""

import os
from pathlib import Path

WORK_SIZE = 1024          # photos are decoded at most this big (faster, less RAM)
CLIP_SIZE = 256           # what the search model looks at
THUMB_SIZE = 320          # photo thumbnails in the results grid
MIN_FACE_PX = 36          # ignore tiny background faces (in WORK_SIZE pixels)
FACE_SCORE = 0.85         # face detector confidence needed
CLUSTER_SIM = 0.40        # how alike two faces must be to group them
ASSIGN_SIM = 0.42         # how alike a face must be to a named person to auto-tag it
ASSIGN_MARGIN = 0.05      # ...and how much better than the next-closest person
MATCH_STRICTNESS = 2.0    # search: how far above a typical photo's score counts as a match
                          # (raise if unrelated photos show up as matches, lower if real ones are missed)
NEAR_STRICTNESS = 1.0     # search: lower bar for the "possibly related" section
MAX_NEAR = 8              # search: at most this many "possibly related" photos
MISSING_SHARE = 0.25      # index: if more than this share of a folder's photos vanish at once...
MISSING_MIN = 50          # ...(and at least this many), keep them: more likely an unplugged drive than a deletion


def data_dir() -> Path:
    """Where the database, models and thumbnails live."""
    if os.environ.get("PHOTOFIND_HOME"):
        base = Path(os.environ["PHOTOFIND_HOME"]).expanduser()
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "photofind"
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        base = Path(xdg) / "photofind"
    base.mkdir(parents=True, exist_ok=True)
    return base
