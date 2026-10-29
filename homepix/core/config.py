"""Settings you might want to tune, and where HomePix keeps its data."""

import os
from pathlib import Path

WORK_SIZE = 1024          # photos are decoded at most this big (faster, less RAM)
CLIP_SIZE = 256           # what the search model looks at
THUMB_SIZE = 320          # photo thumbnails in the results grid
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


OLD_NAME = "photofind"  # HomePix was called Photo Finder: its data folder, files and markers had this name
DB_NAME, OLD_DB_NAME = "homepix.db", f"{OLD_NAME}.db"


def _home() -> Path | None:
    """HOMEPIX_HOME (or Photo Finder's PHOTOFIND_HOME), if set."""
    home = os.environ.get("HOMEPIX_HOME") or os.environ.get("PHOTOFIND_HOME")
    return Path(home).expanduser() if home else None


def _parent() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))


def old_data_dir() -> Path | None:
    """Photo Finder's data folder, while it's still there to move (see move_old_data)."""
    if _home() is not None:
        return None
    old, new = _parent() / OLD_NAME, _parent() / "homepix"
    return old if old.is_dir() and not new.exists() else None


def data_dir() -> Path:
    """Where the database, models and thumbnails live (Photo Finder's folder, until it's moved)."""
    base = _home() or old_data_dir() or _parent() / "homepix"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_file(folder: Path | None = None) -> Path:
    """The database in a data folder (Photo Finder's name for it, in a folder it hasn't been moved from)."""
    folder = folder or data_dir()
    return folder / (OLD_DB_NAME if (folder / OLD_DB_NAME).exists() and not (folder / DB_NAME).exists() else DB_NAME)


def move_old_data() -> bool:
    """Photo Finder's data folder becomes HomePix's: renamed in place (the database in it too), so
    nothing is copied. Only when Photo Finder isn't open. True if it was moved."""
    old = old_data_dir()
    if old is None:
        return False
    new = _parent() / "homepix"
    try:
        old.rename(new)
    except OSError as e:
        print(f"Couldn't move {old} to {new}: {e!r}", flush=True)
        return False
    if (new / OLD_DB_NAME).exists() and not (new / DB_NAME).exists():
        for ext in ("-wal", "-shm", ""):  # (the write-ahead log goes with it, or changes in it are lost)
            if (new / (OLD_DB_NAME + ext)).exists():
                (new / (OLD_DB_NAME + ext)).rename(new / (DB_NAME + ext))
    return True
