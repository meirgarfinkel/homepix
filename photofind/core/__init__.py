"""Everything Photo Finder does, without any user interface: the CLI and the app are both built on this.

The names below are the public API; import them from here (`from photofind import core`), not from
the modules, which may be rearranged.
"""

from .ai import ClipImage, ClipText, Faces, ModelDownloadError, ensure_models, model_path
from .config import data_dir
from .db import NewerDatabaseError, from_blob, open_db, to_blob
from .indexer import Indexer
from .library import IGNORE_MARKER, index_folder, scan_folder
from .media import HEIF, IMAGE_EXTS, date_taken, load_photo
from .people import auto_assign, cluster_unnamed, load_faces, named_people, person_centroids
from .search import SearchIndex, split_matches
from .settings import add_folder, library_folders, load_settings, pictures_folder, remove_folder, save_settings
from .thumbs import face_thumb, photo_thumb, prune_thumbs
from .util import be_nice, human_time, say

__all__ = [
    "HEIF", "IGNORE_MARKER", "IMAGE_EXTS", "ClipImage", "ClipText", "Faces", "Indexer", "ModelDownloadError",
    "NewerDatabaseError", "SearchIndex", "add_folder", "auto_assign", "be_nice", "cluster_unnamed",
    "data_dir", "date_taken", "ensure_models", "face_thumb", "from_blob", "human_time", "index_folder",
    "library_folders", "load_faces", "named_people", "load_photo", "load_settings", "model_path", "open_db",
    "person_centroids", "photo_thumb", "pictures_folder", "prune_thumbs", "remove_folder", "save_settings",
    "say", "scan_folder", "split_matches", "to_blob",
]
