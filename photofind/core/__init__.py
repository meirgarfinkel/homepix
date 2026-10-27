"""Everything Photo Finder does, without any user interface: the CLI and the app are both built on this.

The names below are the public API; import them from here (`from photofind import core`), not from
the modules, which may be rearranged.
"""

from .ai import ClipImage, ClipText, Faces, ModelDownloadError, ensure_models, model_path
from .config import data_dir
from .db import NewerDatabaseError, from_blob, open_db, to_blob
from .library import IGNORE_MARKER, index_folder, scan_folder
from .media import HEIF, IMAGE_EXTS, date_taken, load_photo
from .people import auto_assign, cluster_unnamed, load_faces, person_centroids
from .search import split_matches
from .thumbs import face_thumb, photo_thumb, prune_thumbs
from .util import be_nice, human_time, say

__all__ = [
    "HEIF", "IGNORE_MARKER", "IMAGE_EXTS", "ClipImage", "ClipText", "Faces", "ModelDownloadError",
    "NewerDatabaseError", "auto_assign", "be_nice", "cluster_unnamed", "data_dir", "date_taken",
    "ensure_models", "face_thumb", "from_blob", "human_time", "index_folder", "load_faces", "load_photo",
    "model_path", "open_db", "person_centroids", "photo_thumb", "prune_thumbs", "say", "scan_folder",
    "split_matches", "to_blob",
]
