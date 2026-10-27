"""When a photo or video was taken: the one date it's sorted, shown and named by.

It's the rule the library's files were renamed by (the Sep 2026 pipeline), so a file named
2022_04_01_07_32_31.jpg is shown on that day:
1. the earliest real date among the file's name, the photo's own (EXIF DateTimeOriginal and
   CreateDate) and the video's own clock, as it's stored;
2. if there's none, the earliest placeholder date: one tools stamp when they don't know (1 January at
   midnight, or the sRGB colour profile's 1998-02-09 06:49), which still tells the year;
3. if there's none of those either, when the file was last changed.

A video's clock is usually UTC, and that's what its date is (as its name is); some phones put UTC in
photo names too. Matching a Live Photo's clip with its photo needs the time where it happened instead:
its "moment" (None when that's its date anyway).
"""

import datetime as dt
import os
import re

from PIL import Image

# Dates in file names, most precise first
_NAME_DATES = [
    (re.compile(r"(19|20)(\d{2})-(\d{2})-(\d{2}) at (\d{2})[-.](\d{2})[-.](\d{2})"), True),  # macOS screenshots
    (re.compile(r"(19|20)(\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[-_.\s]+(\d{2})[-_.:]?(\d{2})[-_.:]?(\d{2})"), True),
    (re.compile(r"(19|20)(\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)"), False),
]
_SRGB = dt.datetime(1998, 2, 9, 6, 49)  # the sRGB colour profile's own date
_STORED = re.compile(r"(\d{4})[:-](\d\d)[:-](\d\d)[ T](\d\d):(\d\d):(\d\d)")
# A name like the organizer gives: 2022_04_01_07_32_31, maybe with _1, _2 ... after it
ORGANIZED = re.compile(r"^\d{4}_\d\d_\d\d_\d\d_\d\d_\d\d(_\d+)*$")
FORMAT = "%Y_%m_%d_%H_%M_%S"
# Which source wins when two say the same moment
_RANK = {"exif": 0, "video": 1, "filename": 2}


def _plausible(d: dt.datetime) -> bool:
    return 1990 <= d.year <= dt.date.today().year + 1


def is_placeholder(d: dt.datetime) -> bool:
    """A date tools stamp when they have none: never a real one."""
    return d == _SRGB or (d.month == 1 and d.day == 1 and d.time() == dt.time())


def in_name(name: str):
    """The date (and time, if there is one) in a file's name, placeholders too, or None."""
    for pattern, has_time in _NAME_DATES:
        for m in pattern.finditer(name):
            g = [int(x) for x in m.groups()]
            try:
                d = dt.datetime(g[0] * 100 + g[1], g[2], g[3], *(g[4:7] if has_time else ()))
            except ValueError:
                continue
            if _plausible(d):
                return d
    return None


def parse(text):
    """A stored date ("2024:07:01 09:30:05", maybe with more after it) -> datetime, or None."""
    m = _STORED.match(str(text or "").strip())
    if not m:
        return None
    try:
        d = dt.datetime(*map(int, m.groups()))
    except ValueError:
        return None
    return d if _plausible(d) else None


def _iso(d: dt.datetime) -> str:
    return d.isoformat(sep=" ", timespec="seconds")


def pick(path, found, mtime: float) -> tuple[str, str]:
    """(date, where it came from) by the rule above. found: [(datetime, "exif" or "video")]."""
    candidates = [(d, src) for d, src in found if d is not None]
    if (named := in_name(os.path.basename(path))) is not None:
        candidates.append((named, "filename"))
    real = [c for c in candidates if not is_placeholder(c[0])]
    if real or candidates:
        d, src = min(real or candidates, key=lambda c: (c[0], _RANK[c[1]]))
        return _iso(d), src if real else "placeholder"
    return _iso(dt.datetime.fromtimestamp(mtime)), "file-modified"


# --- reading it from a file ------------------------------------------------------------------------

def _photo_moment(taken: str, found) -> str | None:
    """A photo's local time: the earliest real date written in it, when its date is another."""
    real = [d for d, _ in found if d is not None and not is_placeholder(d)]
    moment = _iso(min(real)) if real else None
    return moment if moment != taken else None


def _photo_found(path) -> list:
    try:
        with Image.open(path) as img:
            exif = img.getexif().get_ifd(0x8769)  # the Exif IFD
        return [(parse(exif.get(tag)), "exif") for tag in (36867, 36868)]  # DateTimeOriginal, CreateDate
    except Exception:
        return []


def of_file(path, kind: str, mtime: float) -> tuple[str, str, str | None]:
    """(date, source, moment) of a file, read the quick way (no ExifTool)."""
    if kind in ("video", "motion"):
        from .video import video_meta

        meta = video_meta(path)
        taken, source = pick(path, [(meta["clock"], "video")], mtime)
        return taken, source, _iso(meta["taken"]) if meta["taken"] else None
    found = _photo_found(path)
    taken, source = pick(path, found, mtime)
    return taken, source, _photo_moment(taken, found)


def from_tags(path, tags: dict, mtime: float) -> tuple[str, str, str | None]:
    """The same, from what ExifTool read (its family 1 tag names), so both ways agree."""
    if os.path.splitext(str(path))[1].lower() in _video_exts():
        clock = parse(tags.get("QuickTime:CreateDate"))
        moment = parse(tags.get("Keys:CreationDate"))  # local, as recorded
        if moment is None and clock is not None:
            moment = clock.replace(tzinfo=dt.UTC).astimezone().replace(tzinfo=None)
        taken, source = pick(path, [(clock, "video")], mtime)
        return taken, source, _iso(moment) if moment else None
    found = [(parse(tags.get(t)), "exif") for t in ("ExifIFD:DateTimeOriginal", "ExifIFD:CreateDate")]
    taken, source = pick(path, found, mtime)
    return taken, source, _photo_moment(taken, found)


def _video_exts():
    from .video import VIDEO_EXTS

    return VIDEO_EXTS
