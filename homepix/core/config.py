"""Settings you might want to tune, and where HomePix keeps its data."""

import os
from pathlib import Path

WORK_SIZE = 1024          # photos are decoded at most this big (faster, less RAM)
CLIP_SIZE = 256           # what the search model looks at
THUMB_SIZE = 640          # photo thumbnails in the grid (older 320 px ones are replaced in the background)
MIN_FACE_PX = 36          # ignore tiny background faces (in WORK_SIZE pixels)
FACE_SCORE = 0.85         # face detector confidence needed
CLUSTER_SIM = 0.40        # how alike two faces must be to group them
SUBGROUP_SIM = 0.55       # ...and to sit side by side inside a group (Who is this?)
ASSIGN_SIM = 0.55         # how alike a face must be to the faces someone was named on (the 3 most alike,
                          # on average) to be recognized as them...
ASSIGN_MARGIN = 0.08      # ...and how much more than to anyone else's
MAYBE_SIM = 0.40          # a face this alike to someone (but not enough to be recognized) might be them:
                          # asked about right after naming them, and Who is this? leaves it till later
FOLLOW_UP_FACES = 30      # ...at most this many at a time
ASSIGN_MIN_PX = 80       # faces smaller than this (in the pixels they were read from) are never
                          # recognized by themselves: too blurry to tell people apart (they can still be named)
FACE_READ_PX = 112        # a face smaller than this in the WORK_SIZE photo is read again from a bigger decode
FACE_READ_MAX = 4096      # ...of at most this many pixels on its long side
MATCH_STRICTNESS = 2.0    # search: how far above a typical photo's score counts as a match
                          # (raise if unrelated photos show up as matches, lower if real ones are missed)
NEAR_STRICTNESS = 1.0     # search: lower bar for the "possibly related" section
MAX_NEAR = 8              # search: at most this many "possibly related" photos
MISSING_SHARE = 0.25      # index: if more than this share of a folder's photos vanish at once...
MISSING_MIN = 50          # ...(and at least this many), keep them: more likely an unplugged drive than a deletion


DB_NAME = "homepix.db"


def data_dir() -> Path:
    """Where the database, models and thumbnails live."""
    if os.environ.get("HOMEPIX_HOME"):
        base = Path(os.environ["HOMEPIX_HOME"]).expanduser()
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "homepix"
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        base = Path(xdg) / "homepix"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_file() -> Path:
    return data_dir() / DB_NAME
