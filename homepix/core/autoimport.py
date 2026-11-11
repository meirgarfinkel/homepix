"""Auto import: rules that bring new photos and videos in from a watch folder, where some other app
(the HomePix phone app, iCloud for Windows, icloudpd ...) puts them. HomePix itself never goes on the network.

A rule (kept in settings.json) names a watch folder and where its files go: into a folder of the
library as they are, or, with tidy on, named by date into the library's year folders as the
organizer does it. After importing, a file is moved out of the watch folder (the default) or left
there. A file whose content is already in the library isn't imported again: with "move" it goes to
the Trash so the watch folder empties; with "leave" it's left.

A file that comes back to the same place in the watch folder with different content (the phone turned
or edited it) is a new version of the photo it became: it takes that photo's place in the library, and
the old version goes to the Trash. The photo keeps who is in it when only its orientation changed.

What happens to each file is remembered (import_seen), by where it was and by its content, so
nothing is dealt with twice: not a file left in the watch folder, not one that comes back because an
import was undone (an undone import means those files weren't wanted), and not one deleted from the
library since. A file is only taken once it's been still for SETTLE seconds, so half-copied ones
wait. Everything is one journaled operation (fileops), undoable like any other.
"""

import datetime as dt
import fnmatch
import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import dates, duplicates, fileops
from .config import data_dir
from .media import IMAGE_EXTS
from .organize import _destination, _numbered_base
from .safefs import path_key
from .settings import load_settings, save_settings
from .util import quick_hash, within
from .video import VIDEO_EXTS

MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
SETTLE = 120  # seconds a file must have been still before it's taken
PART_FILES = ("*.part", "*.partial", "*.tmp", "*.crdownload", "~$*")  # sync apps' files still arriving
_CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000  # Windows: offline, recall on open, recall on data access

MOVE, LEAVE = "move", "leave"


@dataclass
class Rule:
    name: str
    watch: str
    dest: str = ""  # tidy off: the folder files go into; tidy on: the library folder whose year folders they go to
    auto: bool = True  # import by itself; off: new files wait to be reviewed
    tidy: bool = False  # name by date and file into year folders
    after: str = MOVE  # MOVE out of the watch folder, or LEAVE it there
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


# --- the rules, in settings.json ---------------------------------------------------------------------


def load_rules() -> list[Rule]:
    out = []
    for d in load_settings().get("import_rules", []):
        try:
            out.append(Rule.from_dict(d))
        except TypeError:
            continue  # (one written by a newer HomePix, missing something this one needs)
    return out


def save_rules(rules: list[Rule]):
    s = load_settings()
    s["import_rules"] = [asdict(r) for r in rules]
    s["import_status"] = {k: v for k, v in s.get("import_status", {}).items() if k in {r.id for r in rules}}
    save_settings(s)


def status(rule_id: str) -> dict:
    """What's known about a rule's last check: checked, imported (when), count, waiting (why it can't run)."""
    return dict(load_settings().get("import_status", {}).get(rule_id, {}))


def set_status(rule_id: str, **changes):
    s = load_settings()
    s.setdefault("import_status", {}).setdefault(rule_id, {}).update(changes)
    save_settings(s)


def _now() -> str:
    return dt.datetime.now().isoformat(sep=" ", timespec="seconds")


def problem(rule: Rule, others: list[Rule], folders: list[str]) -> str | None:
    """Why a rule can't be saved as it is (a message to show), or None."""
    if not rule.name.strip():
        return "Give it a name."
    watch = os.path.abspath(rule.watch) if rule.watch else ""
    if not watch or not os.path.isdir(watch):
        return 'Choose an "Import from" folder that exists.'
    if within(watch, [str(data_dir())]) or within(str(data_dir()), [watch]):
        return "That's where HomePix keeps its own files."
    if watch in [os.path.abspath(f) for f in folders]:
        return "That's a whole library folder. Choose a folder inside it, or one outside your library."
    if any(within(f, [watch]) for f in folders):
        return 'The "Import from" folder can\'t contain a library folder.'
    for o in others:
        if o.id != rule.id and (within(watch, [o.watch]) or within(o.watch, [watch])):
            return f'"{o.name}" already imports from that folder.'
    if rule.after not in (MOVE, LEAVE):
        return "Choose whether to empty the folder after importing."
    if rule.tidy:
        if destination_root(rule, folders) is None:
            return "Choose which library folder to import to."
    elif not rule.dest or not within(rule.dest, folders):
        return 'Choose an "Import to" folder inside your library.'
    return None


def destination_root(rule: Rule, folders: list[str]) -> str | None:
    """With tidy on: the library folder whose year folders files go to (the only one, if there's one)."""
    folders = [os.path.abspath(f) for f in folders]
    if rule.dest and os.path.abspath(rule.dest) in folders:
        return os.path.abspath(rule.dest)
    return folders[0] if len(folders) == 1 else None


def waiting(rule: Rule, folders: list[str]) -> str | None:
    """Why a rule can't run just now (a drive unplugged, a folder gone), or None."""
    if not os.path.isdir(rule.watch):
        return 'The "Import from" folder isn\'t connected.'
    where = destination_root(rule, folders) if rule.tidy else rule.dest
    if not where or not os.path.isdir(where):
        return 'The "Import to" folder isn\'t connected.'
    return None


# --- finding what's new -------------------------------------------------------------------------------


def _arriving(name: str) -> bool:
    return name.startswith(".") or any(fnmatch.fnmatch(name.lower(), p) for p in PART_FILES)


def _cloud_only(st) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _CLOUD_ONLY)


def settled_files(watch: str, now: float | None = None, should_stop=lambda: False) -> list[tuple[str, os.stat_result]]:
    """The photos and videos in a watch folder (and its folders) ready to be taken: not hidden, not still
    arriving, not cloud-only placeholders, and still for SETTLE seconds."""
    now = time.time() if now is None else now
    out = []
    for dirpath, dirnames, filenames in os.walk(watch):
        if should_stop():
            break
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if _arriving(name) or os.path.splitext(name)[1].lower() not in MEDIA_EXTS:
                continue
            p = os.path.join(dirpath, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            # (a sync app gives a file its old time when it's done; its change time says when that was. A
            # time later than now, from a phone or camera with its clock wrong, is left out: it would hold
            # the file back until then)
            last = max((t for t in (st.st_mtime, st.st_ctime) if t <= now), default=now)
            if _cloud_only(st) or now - last < SETTLE:
                continue
            out.append((p, st))
    return out


def looks(rules: list[Rule], now: float | None = None) -> tuple:
    """What a check would find in the rules' watch folders, in short (each file ready to be taken, by
    name, size and time): when it's the same as last time, there's nothing new to check. Only lists
    the folders; no file is read."""
    return tuple((r.watch, p, st.st_size, st.st_mtime) for r in rules for p, st in settled_files(r.watch, now))


_hashes: dict[tuple, str] = {}  # (path, size, time) -> its hash: files waiting for review aren't read at every check


def _sha256(path: str, st=None) -> str:
    key = (path, st.st_size, st.st_mtime) if st is not None else None
    if key in _hashes:
        return _hashes[key]
    d = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            d.update(chunk)
    if key is not None:
        if len(_hashes) > 50_000:
            _hashes.clear()
        _hashes[key] = d.hexdigest()
    return d.hexdigest()


@dataclass
class Incoming:
    path: str
    size: int
    mtime: float
    sha256: str
    op_id: int | None = None  # for one left alone: the undone import it was part of


@dataclass
class Batch:
    """What checking a rule found, and the steps to deal with it."""

    rule: Rule
    imports: list[tuple[Incoming, str]] = field(default_factory=list)  # (file, where it goes)
    updates: list[tuple[Incoming, str]] = field(default_factory=list)  # (new version, the photo it replaces)
    already: list[Incoming] = field(default_factory=list)  # content already in the library
    unwanted: list[Incoming] = field(default_factory=list)  # part of an import that was undone: left alone
    cant: list[tuple[str, str]] = field(default_factory=list)  # (path, why) couldn't be read
    steps: list[dict] = field(default_factory=list)

    def counts(self) -> tuple[int, int]:
        """(photos, videos) to import."""
        videos = sum(1 for f, _ in self.imports if os.path.splitext(f.path)[1].lower() in VIDEO_EXTS)
        return len(self.imports) - videos, videos

    def summary(self) -> str:
        return summary([self])

    def without(self, paths) -> "Batch":
        """The same, leaving out some of the files to import (unticked in a review): they're left as
        they are, and offered again next time."""
        out = {path_key(p) for p in paths}
        b = Batch(
            self.rule,
            [(f, d) for f, d in self.imports if path_key(f.path) not in out],
            self.updates,
            self.already,
            self.unwanted,
            self.cant,
        )
        b.steps = _steps(b)
        return b


def _known(db, path: str, st) -> bool:
    """Has this very file (same place, size and time) been dealt with before?"""
    r = db.execute("SELECT size, mtime FROM import_seen WHERE path=?", (path,)).fetchone()
    return r is not None and r["size"] == st.st_size and abs((r["mtime"] or 0) - st.st_mtime) < 1e-3


def _in_library(db, path: str, size: int, sha: str, watch: str) -> bool:
    """Is this content in the library already? (Not counting the Import from folder itself: when it's
    inside the library, its photos are indexed, but they're on their way in.)"""
    rows = db.execute("SELECT id, path, sha256 FROM photos WHERE qhash=?", (quick_hash(Path(path), size),)).fetchall()
    rows = [r for r in rows if not within(r["path"], [watch])]
    return bool(rows) and sha in duplicates._sha256(db, rows).values()


def _before(db, sha: str):
    """This content's history: "imported" (dealt with before, and since deleted from the library, say),
    ("unwanted", the undone import), or None (never seen)."""
    rows = db.execute(
        "SELECT s.op_id, o.state FROM import_seen s LEFT JOIN ops o ON o.id = s.op_id WHERE s.sha256=?", (sha,)
    ).fetchall()
    if not rows:
        return None
    undone = [r["op_id"] for r in rows if r["state"] == "undone"]
    if len(undone) == len(rows):
        return "unwanted", undone[0]
    return "imported"


class _Trail:
    """Where files went: every finished move, rename, Trash and restore in the history, to follow a
    file from where an import put it to where it is now."""

    def __init__(self, db):
        self.steps: dict[str, list[tuple[int, str]]] = {}
        for r in db.execute(
            "SELECT id, src, dst FROM op_steps WHERE done=? AND action IN ('move', 'trash', 'restore') "
            "AND dst IS NOT NULL ORDER BY id",
            (fileops.FINISHED,),
        ):
            self.steps.setdefault(r["src"], []).append((r["id"], r["dst"]))

    def follow(self, path: str, after: int) -> str:
        """Where the file at path after step `after` is now."""
        while True:
            later = next(((i, d) for i, d in self.steps.get(path, ()) if i > after), None)
            if later is None:
                return path
            after, path = later


def _imported(db, path: str = None):
    """The finished imports of files from a watch folder (just the one from path, if given): rows of
    (where it was, the step that brought it in, where that put it)."""
    where = "AND s.path = ?" if path else ""
    return db.execute(
        "SELECT s.path, st.id, st.dst FROM import_seen s JOIN ops o ON o.id = s.op_id "
        "JOIN op_steps st ON st.op_id = s.op_id AND st.src = s.path "
        f"WHERE o.state != 'undone' AND st.done = ? AND st.action IN ('move', 'copy') {where} ORDER BY st.id",
        (fileops.FINISHED, path) if path else (fileops.FINISHED,),
    ).fetchall()


def where_now(db, path: str, trail: _Trail | None = None) -> str | None:
    """Where the file that came in from path is in the library now, followed through the moves,
    renames, Trash and restores since. None if it never came in, its import was undone, or it's no
    longer in the library (deleted, or its history was cleared)."""
    rows = _imported(db, path)
    if not rows:
        return None
    trail = trail or _Trail(db)
    now = trail.follow(rows[-1]["dst"], rows[-1]["id"])
    there = db.execute("SELECT 1 FROM photos WHERE path=?", (now,)).fetchone()
    return now if there and os.path.isfile(now) else None


def came_from(db, prefix: str, watch: str | None = None) -> list[tuple[str, str]]:
    """(where it is now, where it came from) for each photo auto import brought into the library folder
    at prefix and that's still there. Where it came from is its place in the automation's Import from
    folder (Camera/PXL_1.jpg), so the phone it came from can find its own copy, even after the file
    changed on either side. watch: only those imported from that folder (one phone's)."""
    trail = _Trail(db)
    watches = [r.watch for r in load_rules()]
    here = {r[0] for r in db.execute("SELECT path FROM photos WHERE substr(path, 1, ?) = ?", (len(prefix), prefix))}
    out = []
    for r in _imported(db):
        if watch is not None and not within(r["path"], [watch]):
            continue
        now = trail.follow(r["dst"], r["id"])
        if now not in here:
            continue
        watch = next((w for w in watches if within(r["path"], [w])), None)
        src = os.path.relpath(r["path"], watch) if watch else os.path.basename(r["path"])
        out.append((now, src.replace(os.sep, "/")))
    return out


def check(db, rule: Rule, folders: list[str], now=None, should_stop=lambda: False) -> Batch:
    """What's new in a rule's watch folder, and what to do with it (nothing is done here)."""
    batch, shas = Batch(rule), set()
    trail = None  # (read only if a file came back changed)
    for path, st in settled_files(rule.watch, now, should_stop):
        if should_stop():
            break
        if _known(db, path, st):
            continue
        try:
            sha = _sha256(path, st)
        except OSError as e:
            batch.cant.append((path, e.strerror or str(e)))
            continue
        f = Incoming(path, st.st_size, st.st_mtime, sha)
        history = _before(db, sha)
        if isinstance(history, tuple):
            f.op_id = history[1]
            batch.unwanted.append(f)
        elif history == "imported" or sha in shas or _in_library(db, path, st.st_size, sha, rule.watch):
            batch.already.append(f)
        elif db.execute("SELECT 1 FROM import_seen WHERE path=?", (path,)).fetchone() and (
            prev := where_now(db, path, trail := trail or _Trail(db))
        ):
            shas.add(sha)
            batch.updates.append((f, prev))  # it came in before, and has changed since
        else:
            shas.add(sha)
            batch.imports.append((f, ""))
    batch.imports = _destinations(rule, folders, [f for f, _ in batch.imports])
    batch.steps = _steps(batch)
    return batch


def _taken(path: str, mtime: float):
    """When it was taken, or None if there's no real date (only when the file last changed)."""
    kind = "video" if os.path.splitext(path)[1].lower() in VIDEO_EXTS else "photo"
    try:
        taken, source, _ = dates.quick_file_date(path, kind, mtime)
    except Exception:
        return None
    if source in ("file-modified", "placeholder"):
        return None
    return dt.datetime.fromisoformat(taken)


def _destinations(rule: Rule, folders: list[str], files: list[Incoming]) -> list[tuple[Incoming, str]]:
    """Where each file goes. A photo and a video with the same name in the same folder (a Live Photo)
    go together under one name; a name that's taken gets _1, _2 ... for both."""
    groups: dict[tuple, list[Incoming]] = {}
    for f in files:
        folder, name = os.path.split(f.path)
        groups.setdefault((path_key(folder), os.path.splitext(name)[0].lower()), []).append(f)
    root = destination_root(rule, folders)
    planned, out = set(), []
    for members in groups.values():
        members.sort(key=lambda f: os.path.splitext(f.path)[1].lower() in VIDEO_EXTS)  # the photo leads
        lead = members[0]
        if rule.tidy:
            folder, base, _ = _destination(root, lead.path, _taken(lead.path, lead.mtime))
            exts = [os.path.splitext(f.path)[1].lower() for f in members]
        else:
            folder, base = os.path.abspath(rule.dest), os.path.splitext(os.path.basename(lead.path))[0]
            exts = [os.path.splitext(f.path)[1] for f in members]
        n = 0
        while True:
            stem = base if n == 0 else f"{_numbered_base(base)}_{n}"
            targets = [os.path.join(folder, stem + e) for e in exts]
            keys = [path_key(t) for t in targets]
            if len(set(keys)) == len(keys) and all(
                k not in planned and not os.path.lexists(t) for k, t in zip(keys, targets, strict=True)
            ):
                break
            n += 1
        planned.update(keys)
        out += list(zip(members, targets, strict=True))
    return out


def _steps(batch: Batch) -> list[dict]:
    action = "move" if batch.rule.after == MOVE else "copy"
    steps = []
    for f, prev in batch.updates:  # the old version goes to the Trash first, making room (fileops)
        steps += [{"action": "trash", "src": prev, "dst": None}, {"action": action, "src": f.path, "dst": prev}]
    steps += [{"action": action, "src": f.path, "dst": dst} for f, dst in batch.imports]
    if batch.rule.after == MOVE:
        steps += [{"action": "trash", "src": f.path, "dst": None} for f in batch.already]
    return fileops.with_sidecars(steps)


# --- doing it ---------------------------------------------------------------------------------------------


def _remember(db, files: list[Incoming], op_id):
    db.executemany(
        "INSERT OR REPLACE INTO import_seen (path, size, mtime, sha256, op_id, seen) VALUES (?,?,?,?,?,?)",
        [(f.path, f.size, f.mtime, f.sha256, f.op_id if op_id is None else op_id, _now()) for f in files],
    )


def summary(batches: list[Batch]) -> str:
    """What Undo calls a check's imports: "Import 37 photos from Dad's phone", "Import 40 photos and 2
    videos from Dad's phone and Mom's phone"."""
    photos = videos = already = updated = 0
    names = []
    for b in batches:
        p, v = b.counts()
        photos, videos, already, updated = photos + p, videos + v, already + len(b.already), updated + len(b.updates)
        if p or v or b.updates or (b.already and b.rule.after == MOVE):
            names.append(b.rule.name)
    what = " and ".join(f"{n:,} {w}{'s' if n != 1 else ''}" for n, w in ((photos, "photo"), (videos, "video")) if n)
    where = " and ".join(names) if len(names) < 3 else f"{len(names)} automations"
    if what and updated:
        return f"Import {what} and update {updated:,} from {where}"
    if updated:
        return f"Update {updated:,} {'photo' if updated == 1 else 'photos'} from {where}"
    if not what:
        return f"Clear {already:,} already imported from {where}"
    return f"Import {what} from {where}"


def run(db, batches, progress=None, should_stop=None) -> fileops.Result | None:
    """Do what check() planned (for one rule or several), as one operation, and remember what was dealt
    with. None if there was nothing to do. A file that couldn't be imported isn't remembered: it's tried
    again next time."""
    batches = [batches] if isinstance(batches, Batch) else list(batches)
    # left alone and nothing to do with them: remembered now, so they aren't read again next time
    for b in batches:
        _remember(db, b.unwanted + ([] if b.rule.after == MOVE else b.already), None)
    db.commit()
    steps = [s for b in batches for s in b.steps]
    if not steps:
        return None
    result = fileops.run(db, "import", summary(batches), steps, progress, should_stop)
    finished = {
        r["src"]
        for r in db.execute("SELECT src FROM op_steps WHERE op_id=? AND done=?", (result.op_id, fileops.FINISHED))
    }
    for b in batches:
        for f, prev in b.updates:
            if f.path in finished:
                _carried_over(db, result.op_id, f.path, prev)
        handled = [f for f, _ in b.imports + b.updates] + (b.already if b.rule.after == MOVE else [])
        _remember(db, [f for f in handled if f.path in finished], result.op_id)
    db.commit()
    result.done_paths = finished
    return result


def _picture(path: str):
    """A photo's orientation and a digest of its pixels as stored (before turning), to tell a new
    version that's only turned from one that's been edited. None if it can't be read."""
    from PIL import Image

    from . import media  # noqa: F401  (registers the HEIC reader)

    try:
        with Image.open(path) as im:
            return im.getexif().get(0x0112, 1), hashlib.sha256(im.tobytes()).hexdigest()
    except Exception:
        return None


def _carried_over(db, op_id: int, src: str, prev: str):
    """A new version has taken its photo's place: it keeps the old one's row (who is in it), turned
    if only its orientation changed. If its place was taken meanwhile, it came in under a new name,
    as a new photo, and is left to be indexed."""
    went = db.execute(
        "SELECT dst FROM op_steps WHERE op_id=? AND src=? AND action IN ('move', 'copy') AND done=?",
        (op_id, src, fileops.FINISHED),
    ).fetchone()
    old = db.execute(
        "SELECT dst, before FROM op_steps WHERE op_id=? AND src=? AND action='trash' AND done=?",
        (op_id, prev, fileops.FINISHED),
    ).fetchone()
    if went is None or went["dst"] != prev or old is None:
        return
    snap = json.loads(old["before"] or "{}").get("snapshot")
    was, now = _picture(old["dst"]), _picture(prev)  # (the old version is in the Trash now)
    same = was is not None and now is not None and was[1] == now[1]
    fileops.carry_over(db, prev, snap, (was[0], now[0]) if same else None)


@dataclass
class Checked:
    """What checking the rules found and did."""

    batches: list[Batch]  # one per rule that could be checked
    result: fileops.Result | None  # the import done (rules that import by themselves), if any

    def imported(self, batch: Batch) -> int:
        """How many of a rule's files came into the library."""
        return sum(self.came_in(batch))

    def came_in(self, batch: Batch) -> tuple[int, int]:
        """(photos, videos) of a rule's that came into the library."""
        done = getattr(self.result, "done_paths", set())
        exts = [os.path.splitext(f.path)[1].lower() for f, _ in batch.imports if f.path in done]
        videos = sum(1 for e in exts if e in VIDEO_EXTS)
        return len(exts) - videos, videos


def check_rules(db, rules: list[Rule], folders: list[str], now=None, progress=None, should_stop=lambda: False):
    """Check every rule that's on: import what's new for those that do it by themselves (all as one
    operation), count it for those that wait for review, and note each rule's status."""
    batches = []
    for r in rules:
        if not r.enabled:
            continue
        why = waiting(r, folders)
        if why:
            set_status(r.id, checked=_now(), waiting=why)
            continue
        batches.append(check(db, r, folders, now, should_stop))
    out = Checked(batches, run(db, [b for b in batches if b.rule.auto], progress, should_stop))
    for b in batches:
        st = {"checked": _now(), "waiting": None, "pending": 0 if b.rule.auto else len(b.imports) + len(b.updates)}
        if b.rule.auto and (n := out.imported(b)):
            st.update(imported=_now(), count=n)
        set_status(b.rule.id, **st)
    return out
