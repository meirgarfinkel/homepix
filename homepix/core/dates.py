"""When a photo or video was taken: the one date it's sorted, shown and named by. Every date is the
time on the clock where it was taken (local time):
1. the earliest real date among the file's name, the photo's own (EXIF DateTimeOriginal and
   CreateDate) and the video's own clock (stored in UTC, so turned into local time). A name and the
   file that say the same moment in different time zones (hours apart, to the quarter hour: a Pixel's
   PXL_ name is UTC, and so are names the organizer gave before 1.17) are one date, the file's; a name
   with only a day never beats a time on that day;
2. if there's none, the earliest placeholder date: one tools stamp when they don't know (1 January at
   midnight, or the sRGB colour profile's 1998-02-09 06:49), which still tells the year;
3. if there's none of those either, when the file was last changed.

Matching a Live Photo's clip with its photo uses its "moment" (None when that's its date anyway).
"""

import datetime as dt
import os
import re

from PIL import Image

from .video import is_video, video_meta

# Dates in file names, most precise first
_NAME_DATES = [
    (re.compile(r"(19|20)(\d{2})-(\d{2})-(\d{2}) at (\d{2})[-.](\d{2})[-.](\d{2})"), True),  # macOS screenshots
    (re.compile(r"(19|20)(\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[-_.\s]+(\d{2})[-_.:]?(\d{2})[-_.:]?(\d{2})"), True),
    # 2026-10-01_09-09: hours and minutes, no seconds (a separator between them, so a number isn't one)
    (re.compile(r"(19|20)(\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[-_.\s]+(\d{2})[-_.:h](\d{2})(?![\d:.-]\d)"), True),
    (re.compile(r"(19|20)(\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)"), False),
]
_SRGB = dt.datetime(1998, 2, 9, 6, 49)  # the sRGB colour profile's own date
_STORED = re.compile(r"(\d{4})[:-](\d\d)[:-](\d\d)[ T](\d\d):(\d\d):(\d\d)")
# A name like the organizer gives: 2022_04_01_07_32_31, maybe with _1, _2 ... after it
ORGANIZED = re.compile(r"^\d{4}_\d\d_\d\d_\d\d_\d\d_\d\d(_\d+)*$")
FORMAT = "%Y_%m_%d_%H_%M_%S"
# Which source wins when two say the same moment
_RANK = {"exif": 0, "video": 1, "filename": 2}


def _plausible_date(d: dt.datetime) -> bool:
    return 1990 <= d.year <= dt.date.today().year + 1


def is_placeholder(d: dt.datetime) -> bool:
    """A date tools stamp when they have none: never a real one."""
    return d == _SRGB or (d.month == 1 and d.day == 1 and d.time() == dt.time())


def _name_date(name: str) -> tuple[dt.datetime | None, bool]:
    """(the date in a file's name, placeholders too, or None; whether it has a time)."""
    for pattern, has_time in _NAME_DATES:
        for m in pattern.finditer(name):
            g = [int(x) for x in m.groups()]
            try:
                d = dt.datetime(g[0] * 100 + g[1], g[2], g[3], *(g[4:7] if has_time else ()))
            except ValueError:
                continue
            if _plausible_date(d):
                return d, has_time
    return None, False


def date_in_name(name: str):
    """The date (and time, if there is one) in a file's name, placeholders too, or None."""
    return _name_date(name)[0]


def to_local(utc: dt.datetime) -> dt.datetime:
    """A UTC time as this computer's clock showed it then (summer time included)."""
    return utc.replace(tzinfo=dt.UTC).astimezone().replace(tzinfo=None)


def same_moment(a: dt.datetime, b: dt.datetime) -> bool:
    """Could a and b be one moment, written in two time zones? (A quarter hour to 14 hours apart, to the
    quarter hour, the seconds within 5. Seconds apart is one clock, not two zones.)"""
    gap = abs((a - b).total_seconds())
    off = gap % 900
    return 895 <= gap <= 14 * 3600 + 5 and (off <= 5 or off >= 895)


def parse(text, any_year: bool = False):
    """A stored date ("2024:07:01 09:30:05", maybe with .123 or +02:00 after it) -> datetime, or None.
    Unless any_year, only a plausible one (1990 to next year) counts."""
    m = _STORED.match(str(text or "").strip())
    if not m:
        return None
    try:
        d = dt.datetime(*map(int, m.groups()))
    except ValueError:
        return None
    return d if any_year or _plausible_date(d) else None


def _iso(d: dt.datetime) -> str:
    return d.isoformat(sep=" ", timespec="seconds")


def best_date(path, found, mtime: float) -> tuple[str, str]:
    """(date, where it came from) by the rule above. found: [(datetime, "exif" or "video")], local time."""
    candidates = [(d, src) for d, src in found if d is not None]
    name = os.path.basename(path)
    named, has_time = _name_date(name)
    if named is not None and has_time and name.upper().startswith("PXL_"):
        named = to_local(named)  # (a Pixel names its photos and videos by UTC)
    in_file = [d for d, _ in candidates if not is_placeholder(d)]
    if named is not None and in_file and not is_placeholder(named):
        first = min(in_file)
        if not has_time and named.date() == first.date():
            named = None  # a day alone never beats a time on that day
        elif has_time and named != first and same_moment(named, first):
            camera = not ORGANIZED.match(os.path.splitext(name)[0]) and not name.upper().startswith("PXL_")
            if any(src == "video" for _, src in candidates) and camera:
                return _iso(named), "filename"  # (a camera that wrote local time as if it were UTC)
            named = None  # one moment in two time zones: the file's own, local, time
    if named is not None:
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


def _video_local(clock, moment):
    """A video's date in local time: where it was taken (moment), or its UTC clock turned local. A
    placeholder clock stays as it is, so it's still known for one."""
    if clock is not None and is_placeholder(clock):
        return clock
    return moment or clock


def quick_file_date(path, kind: str, mtime: float) -> tuple[str, str, str | None]:
    """(date, source, moment) of a file, read the quick way (no ExifTool)."""
    if kind in ("video", "motion"):
        meta = video_meta(path)
        taken, source = best_date(path, [(_video_local(meta["clock"], meta["taken"]), "video")], mtime)
        return taken, source, _iso(meta["taken"]) if meta["taken"] else None
    found = _photo_found(path)
    taken, source = best_date(path, found, mtime)
    return taken, source, _photo_moment(taken, found)


def from_tags(path, tags: dict, mtime: float) -> tuple[str, str, str | None]:
    """The same, from what ExifTool read (its family 1 tag names), so both ways agree."""
    if is_video(path):
        clock = parse(tags.get("QuickTime:CreateDate"))
        moment = parse(tags.get("Keys:CreationDate"))  # local, as recorded
        if moment is None and clock is not None:
            moment = clock.replace(tzinfo=dt.UTC).astimezone().replace(tzinfo=None)
        taken, source = best_date(path, [(_video_local(clock, moment), "video")], mtime)
        return taken, source, _iso(moment) if moment else None
    found = [(parse(tags.get(t)), "exif") for t in ("ExifIFD:DateTimeOriginal", "ExifIFD:CreateDate")]
    taken, source = best_date(path, found, mtime)
    return taken, source, _photo_moment(taken, found)
