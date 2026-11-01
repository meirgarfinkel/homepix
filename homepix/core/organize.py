"""The organizer: each file named by when it was taken (2022_04_01_07_32_31.jpg, then _1, _2 ... for
others taken in the same second) and kept in its year's folder (<library>/2022/); a file with no real
date (only when it was last changed) goes to <library>/Unsorted/ with its own name.

The date is the one the app shows everywhere (dates.py), so organizing agrees with the timeline. A
Live Photo's clip and a photo's sidecar files (.aae, .xmp, .json) are named and moved with it. A year
folder's own subfolders are left alone (a file in 2022/Trip/ from 2022 stays there). Nothing is done
here: plan() works out the moves, which fileops.run() makes, and which can be undone like any other.
"""

import datetime as dt
import os
from dataclasses import dataclass

from . import dates
from .fileops import companions
from .safefs import path_key
from .util import within

UNSORTED = "Unsorted"
SIDECAR_EXTS = (".aae", ".xmp", ".json")


@dataclass
class Change:
    src: str
    dst: str
    why: str  # "renamed", "to 2022", "to Unsorted" or "with its photo"


@dataclass
class Plan:
    changes: list[Change]
    looked_at: int  # files it considered
    not_ready: int  # files still being indexed: they have no date yet, so they're left for now

    def as_steps(self) -> list[dict]:
        return [{"action": "move", "src": c.src, "dst": c.dst} for c in self.changes]


def root_of(path, folders) -> str | None:
    """The library folder path is in (the innermost, if one holds another)."""
    path = os.path.abspath(path)
    inside = [
        os.path.abspath(f)
        for f in folders
        if path == os.path.abspath(f) or path.startswith(os.path.join(os.path.abspath(f), ""))
    ]
    return max(inside, key=len) if inside else None


class _Sidecars:
    """A folder's sidecar files, read once."""

    def __init__(self):
        self._folders: dict[str, dict[str, str]] = {}
        self.used: set[str] = set()

    def sidecars_of(self, path: str) -> list[tuple[str, str]]:
        """[(sidecar path, what follows the photo's new stem in its name)] for a photo or video:
        IMG_1.AAE and IMG_1.xmp (named by its stem) or IMG_1.JPG.json (by its whole name)."""
        folder, name = os.path.split(path)
        if folder not in self._folders:
            try:
                names = [n for n in os.listdir(folder) if os.path.splitext(n)[1].lower() in SIDECAR_EXTS]
            except OSError:
                names = []
            self._folders[folder] = {n.lower(): n for n in names}
        here = self._folders[folder]
        stem, ext = os.path.splitext(name)
        out = []
        for side_ext in SIDECAR_EXTS:
            for base, kept in ((stem, ""), (name, ext.lower())):
                found = here.get((base + side_ext).lower())
                if found is None:
                    continue
                full = os.path.join(folder, found)
                if full not in self.used:
                    self.used.add(full)
                    out.append((full, kept + side_ext))
        return out


@dataclass
class _Group:
    """A file and the ones that go with it, moved and named together."""

    lead: str
    taken: dt.datetime | None
    folder: str  # where they belong
    base: str  # the stem they'd be named by
    members: list[tuple[str, str, str]]  # (path, what follows the stem, why)

    def paths(self, stem: str) -> list[str]:
        return [os.path.join(self.folder, stem + tail) for _, tail, _ in self.members]

    def unchanged(self) -> bool:
        return all(src == dst for (src, _, _), dst in zip(self.members, self.paths(self.base), strict=True))


def _destination(root: str, path: str, taken: dt.datetime | None) -> tuple[str, str, str]:
    """(the folder it belongs in, the stem it'd be named by, why it moves if it does)."""
    folder, name = os.path.split(path)
    stem = os.path.splitext(name)[0]
    if taken is None:  # no real date
        unsorted = os.path.join(root, UNSORTED)
        if within(folder, [unsorted]):
            return folder, stem, "renamed"
        return unsorted, stem, f"to {UNSORTED}"
    year = os.path.join(root, str(taken.year))
    in_place = within(folder, [year])
    named = taken.strftime(dates.FORMAT)
    if in_place and dates.ORGANIZED.match(stem) and stem[: len(named)] == named:
        named = stem  # already named so, maybe with its _1: kept
    return (folder if in_place else year), named, ("renamed" if in_place else f"to {taken.year}")


def plan(db, folders, scope=None) -> Plan:
    """What organizing would do. folders: the library's folders; scope: a folder in the library to
    organize (its subfolders too), or None for the whole library."""
    roots = [os.path.abspath(f) for f in folders]
    scope = os.path.abspath(scope) if scope else None
    if scope and root_of(scope, roots) is None:
        raise ValueError(f"{scope} isn't in the library")
    files, not_ready = _files(db, roots, scope)
    moving = [g for g in _groups(db, files) if not g.unchanged()]
    return Plan(_with_free_names(moving), len(files), not_ready)


def _files(db, roots, scope):
    """The files to organize, as (path, kind, root, date taken or None), and how many aren't indexed yet."""
    where, params = "", ()
    if scope:
        where, params = " AND (path LIKE ? ESCAPE '\\')", (_like(os.path.join(scope, "")),)
    rows = db.execute(
        "SELECT path, kind, taken, taken_source, qhash, error FROM photos WHERE 1" + where + " ORDER BY taken, path",
        params,
    ).fetchall()
    files, not_ready = [], 0
    for r in rows:
        root = root_of(r["path"], roots)
        if root is None:
            continue
        if r["error"] is not None:
            continue  # unreadable: left as it is
        if r["qhash"] is None or r["taken"] is None:
            not_ready += 1
            continue
        real = r["taken_source"] not in (None, "file-modified")
        files.append((r["path"], r["kind"], root, dt.datetime.fromisoformat(r["taken"]) if real else None))
    return files, not_ready


def _groups(db, files) -> list:
    """Each photo or video with what goes along with it: a Live Photo's clip, its sidecars."""
    clips = companions(db, [p for p, kind, _, _ in files if kind == "photo"])
    with_photo = set(clips.values())
    sidecars, groups = _Sidecars(), []
    for path, _kind, root, taken in files:
        if path in with_photo:
            continue
        folder, base, why = _destination(root, path, taken)
        members = [(path, os.path.splitext(path)[1].lower(), why)]
        if path in clips:
            members.append((clips[path], os.path.splitext(clips[path])[1].lower(), "with its photo"))
        members += [(side, tail.lower(), "with its photo") for side, tail in sidecars.sidecars_of(path)]
        groups.append(_Group(path, taken, folder, base, members))
    return groups


def _with_free_names(moving) -> list:
    """The changes, each group under its name or, where that's taken, the first free numbered one."""
    # (names compared as the system does: on Windows IMG.JPG and img.jpg are one name)
    leaving = {path_key(src) for g in moving for src, _, _ in g.members}
    planned, changes = set(), []
    for g in moving:
        n = 0
        while True:
            stem = g.base if n == 0 else f"{_numbered_base(g.base)}_{n}"
            targets = g.paths(stem)
            keys = [path_key(t) for t in targets]
            free = all(
                k not in planned and (k in leaving or not os.path.lexists(t))
                for k, t in zip(keys, targets, strict=True)
            )
            if free:
                break
            n += 1
        planned.update(keys)
        changes += [Change(src, dst, why) for (src, _, why), dst in zip(g.members, targets, strict=True) if src != dst]
    return changes


def _numbered_base(stem: str) -> str:
    """The date part of an organized name (its _1 dropped), so another number goes after the date."""
    if dates.ORGANIZED.match(stem):
        return stem[:19]
    return stem


def _like(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
