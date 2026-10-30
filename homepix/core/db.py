"""The database (one SQLite file): its schema, and bringing an older one up to date.

The schema version is SQLite's user_version. A new database gets TABLES and INDEXES. An older one is
first backed up to backups/, then upgraded inside a single locked transaction, so a failed upgrade
leaves it untouched. A database from a newer HomePix, or from before 1.0, is refused, never modified.

To change the schema: change TABLES (or INDEXES), bump VERSION, and add to UPGRADES the step that
brings a database of the version before up to it. Never edit a step that has shipped.
"""

import datetime as dt
import sqlite3

import numpy as np

from .config import data_dir, db_file

VERSION = 12

TABLES = {
    "photos": """CREATE TABLE photos (
        id INTEGER PRIMARY KEY,
        path TEXT UNIQUE NOT NULL,
        size INTEGER, mtime REAL,
        qhash TEXT,  -- fingerprint of its size and first and last 64 KB (util.quick_hash)
        kind TEXT NOT NULL DEFAULT 'photo',  -- photo, video, or motion: a Live Photo's moving part
        taken TEXT,  -- its date (dates.py)
        taken_source TEXT,  -- where that came from: exif, video, filename, placeholder, file-modified
        moment TEXT,  -- the local time it happened, where its date is another (to match a Live Photo's clip)
        duration REAL,  -- seconds, for videos
        clip BLOB,  -- its search vector
        faces_done INTEGER DEFAULT 0,
        error TEXT, error_reader TEXT,  -- why it couldn't be read, and by which reader (media.READER)
        keywords TEXT, description TEXT,  -- written in the file (keywords: a JSON list)
        details_done INTEGER NOT NULL DEFAULT 0,  -- those read from the file since it last changed
        sha256 TEXT,  -- of the whole file: filled in only for those that might be exact copies
        phash INTEGER, width INTEGER, height INTEGER  -- its look and size in pixels, for duplicates
    )""",
    "people": """CREATE TABLE people (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE NOT NULL COLLATE NOCASE
    )""",
    "faces": """CREATE TABLE faces (
        id INTEGER PRIMARY KEY,
        photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
        x REAL, y REAL, w REAL, h REAL, score REAL,  -- the box, as parts of the shown photo
        emb BLOB NOT NULL,
        px REAL,  -- its size in the pixels it was read from (NULL for faces found before 1.0)
        person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
        locked INTEGER DEFAULT 0,  -- named by hand (or said not to be someone): never recognized again
        hidden INTEGER NOT NULL DEFAULT 0,  -- not a face, or a stranger: never grouped, asked about or named
        not_person INTEGER REFERENCES people(id) ON DELETE SET NULL  -- who someone said it isn't
    )""",
    # The file-operations journal (fileops.py): every change, so it can be undone or finished after a crash
    "ops": """CREATE TABLE ops (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        summary TEXT,
        started TEXT NOT NULL,
        finished TEXT,
        state TEXT NOT NULL DEFAULT 'running',
        undoes INTEGER REFERENCES ops(id)  -- an undo (or redo): the operation it takes back
    )""",
    "op_steps": """CREATE TABLE op_steps (
        id INTEGER PRIMARY KEY,
        op_id INTEGER NOT NULL REFERENCES ops(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        action TEXT NOT NULL,
        src TEXT,
        dst TEXT,
        before TEXT,
        after TEXT,
        done INTEGER NOT NULL DEFAULT 0,
        UNIQUE (op_id, seq)
    )""",
    # Pairs of photos someone said aren't duplicates (a < b): never offered again
    "apart": """CREATE TABLE apart (
        a INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
        b INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
        PRIMARY KEY (a, b)
    )""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS photos_qhash ON photos(qhash)",
    "CREATE INDEX IF NOT EXISTS photos_kind ON photos(kind)",
    "CREATE INDEX IF NOT EXISTS photos_sha256 ON photos(sha256)",
    "CREATE INDEX IF NOT EXISTS faces_photo ON faces(photo_id)",
    "CREATE INDEX IF NOT EXISTS faces_person ON faces(person_id)",
]


class DatabaseVersionError(RuntimeError):
    """The database is from a newer HomePix, or from one too old to bring up to date."""


def columns(db, table) -> list[str]:
    return [r[1] for r in db.execute(f"PRAGMA table_info({table})")]


def _rebuild(db, table: str):
    """Make table as TABLES has it, keeping the columns it still has. (SQLite 3.34, the release
    build's, can't drop a column.) Foreign keys must be off."""
    keep = ", ".join(c for c in columns(db, table) if c in set(_columns_of(TABLES[table])))
    db.execute(TABLES[table].replace(f"CREATE TABLE {table} ", f"CREATE TABLE new_{table} ", 1))
    db.execute(f"INSERT INTO new_{table} ({keep}) SELECT {keep} FROM {table}")
    db.execute(f"DROP TABLE {table}")
    db.execute(f"ALTER TABLE new_{table} RENAME TO {table}")


def _columns_of(create: str) -> list[str]:
    """The column names in a CREATE TABLE statement."""
    scratch = sqlite3.connect(":memory:")
    try:
        scratch.execute(create)
        name = create.split()[2]
        return columns(scratch, name)
    finally:
        scratch.close()


def _to_12(db):
    """1.0's leftovers go, none of them used: the star rating (dropped from the app), numbers for face
    groups (the old command line's), a journal step's photo, and the sidecars table."""
    db.execute("DROP TABLE IF EXISTS sidecars")
    for table in ("photos", "faces", "op_steps"):
        _rebuild(db, table)


UPGRADES = {11: _to_12}  # version: the step that brings it up to the next one


def migrate(db: sqlite3.Connection, path):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == VERSION:
        return
    if version > VERSION:
        raise DatabaseVersionError(
            f"{path} was upgraded by a newer HomePix (database version {version}; this one knows up to "
            f"{VERSION}). Use the newer version, or restore a copy from {path.parent / 'backups'}."
        )
    if db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
        backup(db, version)
    upgrade(db)


def upgrade(db: sqlite3.Connection):
    """Bring a database up to VERSION, all in one transaction (no backup: see migrate)."""
    keys = db.execute("PRAGMA foreign_keys").fetchone()[0]
    db.execute("PRAGMA foreign_keys = OFF")  # (tables are rebuilt; only possible outside a transaction)
    db.execute("BEGIN IMMEDIATE")  # one upgrader at a time: two windows may open it together
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]  # someone may have upgraded meanwhile
        empty = not db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if version == 0 and empty:
            for create in TABLES.values():
                db.execute(create)
        elif version < VERSION:
            if version not in UPGRADES:
                raise DatabaseVersionError(
                    f"This library was made by a HomePix from before 1.0 (database version {version}), "
                    "which this one can't bring up to date."
                )
            while version < VERSION:
                UPGRADES[version](db)
                version += 1
        for create in INDEXES:
            db.execute(create)
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DatabaseVersionError("The upgrade would leave rows pointing at others that aren't there.")
        db.execute(f"PRAGMA user_version = {VERSION}")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute(f"PRAGMA foreign_keys = {keys}")


def backup(db: sqlite3.Connection, version: int, why: str = ""):
    """A copy of the database in the backups folder (why: a word for its name, like before-reset)."""
    folder = data_dir() / "backups"
    folder.mkdir(exist_ok=True)
    out = folder / f"homepix-v{version}{'-' + why if why else ''}-{dt.datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(out) as copy:
        db.backup(copy)
    copy.close()
    return out


_ready_dbs = set()


def open_db() -> sqlite3.Connection:
    path = db_file()
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
