"""The database (one SQLite file) and its versioned migrations.

The schema version is SQLite's user_version. Opening an older database first backs it up to
backups/, then upgrades it one version at a time inside a single locked transaction, so a failed
upgrade leaves it untouched. A database from a newer Photo Finder is refused, never modified.

To change the schema, add a migration at the end of MIGRATIONS. Never edit one that has shipped.
"""

import datetime as dt
import sqlite3

import numpy as np

from .config import data_dir


class NewerDatabaseError(RuntimeError):
    """The database was upgraded by a newer Photo Finder than this one."""


def _columns(db, table):
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def _m1_base(db):
    """The schema from before versioning (M0 and earlier); databases from then already have most of it."""
    for stmt in (
        """CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            size INTEGER, mtime REAL, qhash TEXT,
            taken TEXT,
            clip BLOB,
            faces_done INTEGER DEFAULT 0,
            error TEXT,
            error_reader TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS photos_qhash ON photos(qhash)",
        """CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL COLLATE NOCASE
        )""",
        """CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY,
            photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            x REAL, y REAL, w REAL, h REAL, score REAL,
            emb BLOB NOT NULL,
            cluster INTEGER,
            person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
            locked INTEGER DEFAULT 0
        )""",
        "CREATE INDEX IF NOT EXISTS faces_photo ON faces(photo_id)",
        "CREATE INDEX IF NOT EXISTS faces_person ON faces(person_id)",
    ):
        db.execute(stmt)
    if "error_reader" not in _columns(db, "photos"):  # added in M0, before versioning existed
        db.execute("ALTER TABLE photos ADD COLUMN error_reader TEXT")


def _m2_photo_manager(db):
    """What the photo manager needs (see PLAN.md)."""
    for stmt in (
        # 'photo' or 'video'
        "ALTER TABLE photos ADD COLUMN kind TEXT NOT NULL DEFAULT 'photo'",
        # where `taken` came from, e.g. 'exif' or 'file-modified'; NULL = indexed before this was recorded
        "ALTER TABLE photos ADD COLUMN taken_source TEXT",
        # fingerprint of the whole file, for exact duplicates (qhash only samples the start and end)
        "ALTER TABLE photos ADD COLUMN sha256 TEXT",
        "CREATE INDEX photos_sha256 ON photos(sha256)",
        # files that belong with a photo: a Live Photo's video, .aae / .xmp / .json sidecars
        """CREATE TABLE sidecars (
            id INTEGER PRIMARY KEY,
            photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            path TEXT UNIQUE NOT NULL,
            kind TEXT NOT NULL
        )""",
        "CREATE INDEX sidecars_photo ON sidecars(photo_id)",
        # the file-operations journal: every change to files, so it can be undone or finished after a crash
        """CREATE TABLE ops (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            summary TEXT,
            started TEXT NOT NULL,
            finished TEXT,
            state TEXT NOT NULL DEFAULT 'running'
        )""",
        """CREATE TABLE op_steps (
            id INTEGER PRIMARY KEY,
            op_id INTEGER NOT NULL REFERENCES ops(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            action TEXT NOT NULL,
            photo_id INTEGER,
            src TEXT,
            dst TEXT,
            before TEXT,
            after TEXT,
            done INTEGER NOT NULL DEFAULT 0,
            UNIQUE (op_id, seq)
        )""",
    ):
        db.execute(stmt)


def _m3_videos(db):
    """Videos: their length. kind can now also be 'motion': a Live Photo's (or motion photo's) moving
    part, which belongs to the photo taken at the same moment and isn't shown on its own."""
    db.execute("ALTER TABLE photos ADD COLUMN duration REAL")  # seconds, for videos
    db.execute("CREATE INDEX photos_kind ON photos(kind)")


def _m4_undo(db):
    """Undo and redo: an undo (or redo) operation records which operation it takes back."""
    db.execute("ALTER TABLE ops ADD COLUMN undoes INTEGER REFERENCES ops(id)")


def _m5_details(db):
    """Details written in the files themselves, kept in the index to show and filter by: the star
    rating (0-5), keywords (a JSON list) and description. details_done: read from the file since it
    last changed."""
    for stmt in (
        "ALTER TABLE photos ADD COLUMN rating INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE photos ADD COLUMN keywords TEXT",
        "ALTER TABLE photos ADD COLUMN description TEXT",
        "ALTER TABLE photos ADD COLUMN details_done INTEGER NOT NULL DEFAULT 0",
        "CREATE INDEX photos_rating ON photos(rating) WHERE rating > 0",
    ):
        db.execute(stmt)


def _m6_no_rating(db):
    """Star ratings were dropped. The column stays (always 0): removing one needs SQLite 3.35, newer
    than the release build's."""
    db.execute("DROP INDEX IF EXISTS photos_rating")
    db.execute("UPDATE photos SET rating = 0 WHERE rating != 0")


def _m7_dates(db):
    """Dates are worked out by the library's rename rule now (core/dates.py): every file's details are
    read again, dates with them. moment: the local time it happened, where its date is another (a
    video's UTC clock, or a phone's UTC name), to match a Live Photo's clip with its photo."""
    db.execute("ALTER TABLE photos ADD COLUMN moment TEXT")
    db.execute("UPDATE photos SET details_done = 0")


def _m8_faces(db):
    """hidden: a face not to name (not a face at all, or a stranger): never grouped, asked about or
    recognized. locked keeps its meaning: someone said who this isn't, so it's never recognized."""
    db.execute("ALTER TABLE faces ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")


def _m9_duplicates(db):
    """Duplicates: a photo's look (phash, a 64-bit perceptual hash of its thumbnail) and size in
    pixels, filled in after indexing; sha256 (from M2) is filled in only for files that might be exact
    copies. apart: pairs of photos someone said aren't duplicates (a < b), never offered again."""
    for stmt in (
        "ALTER TABLE photos ADD COLUMN phash INTEGER",
        "ALTER TABLE photos ADD COLUMN width INTEGER",
        "ALTER TABLE photos ADD COLUMN height INTEGER",
        """CREATE TABLE apart (
            a INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            b INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            PRIMARY KEY (a, b)
        )""",
    ):
        db.execute(stmt)


def _m10_face_size(db):
    """px: a face's size in the pixels it was read from (a small face is read from a bigger decode of
    its photo), to leave the blurry ones out of recognizing; NULL for faces found before."""
    db.execute("ALTER TABLE faces ADD COLUMN px REAL")


MIGRATIONS = [_m1_base, _m2_photo_manager, _m3_videos, _m4_undo, _m5_details, _m6_no_rating, _m7_dates,
              _m8_faces, _m9_duplicates, _m10_face_size]
LATEST = len(MIGRATIONS)


def migrate(db: sqlite3.Connection, path):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > LATEST:
        raise NewerDatabaseError(
            f"{path} was upgraded by a newer Photo Finder (database version {version}; this one knows up to "
            f"{LATEST}). Use the newer version, or restore a copy from {path.parent / 'backups'}."
        )
    if version == LATEST:
        return
    if db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
        backup(db, version)
    upgrade(db)


def upgrade(db: sqlite3.Connection):
    """Bring a database up to LATEST, all in one transaction (no backup: see migrate)."""
    db.execute("BEGIN IMMEDIATE")  # one upgrader at a time: the app and the CLI may open it together
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]  # someone may have upgraded meanwhile
        for step in MIGRATIONS[version:]:
            step(db)
        db.execute(f"PRAGMA user_version = {LATEST}")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def backup(db: sqlite3.Connection, version: int, why: str = ""):
    """A copy of the database in the backups folder (why: a word for its name, like before-reset)."""
    folder = data_dir() / "backups"
    folder.mkdir(exist_ok=True)
    out = folder / f"photofind-v{version}{'-' + why if why else ''}-{dt.datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(out) as copy:
        db.backup(copy)
    copy.close()
    return out


_ready_dbs = set()


def open_db() -> sqlite3.Connection:
    path = data_dir() / "photofind.db"
    db = sqlite3.connect(path, timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")  # per connection
    if path not in _ready_dbs:  # WAL mode and the schema persist in the file: check them once per run
        db.execute("PRAGMA journal_mode = WAL")
        migrate(db, path)
        _ready_dbs.add(path)
    return db


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float16).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float16).astype(np.float32)
