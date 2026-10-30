"""The details HomePix edits in a photo's own file (description, keywords, date taken, location,
which way up it is), and which tags each one is kept in.

Each detail is written to the standard tag that other photo apps read (XMP for the description
and keywords; EXIF for the date, location and orientation), and to the older tags too where
the file already has them, so they don't disagree. Tags are named with ExifTool's family 1 groups
(IFD0:Orientation, XMP-dc:Subject, ...), so a change reads and writes the same names, and can be
undone by writing back the values it had.

Orientation follows EXIF's eight values. HEIC photos are turned by their own rotation box instead
(viewers ignore EXIF orientation there), so they can be rotated but not flipped.
"""

import datetime as dt
import os
import re
from dataclasses import dataclass, field

from .dates import parse
from .exiftool import exiftool
from .video import is_video

DESCRIPTION = ("XMP-dc:Description", "IPTC:Caption-Abstract", "IFD0:ImageDescription")
KEYWORDS = ("XMP-dc:Subject", "IPTC:Keywords")
PHOTO_DATES = (
    "ExifIFD:DateTimeOriginal",
    "ExifIFD:CreateDate",
    "XMP-exif:DateTimeOriginal",
    "XMP-xmp:CreateDate",
    "XMP-photoshop:DateCreated",
)
VIDEO_DATES = ("QuickTime:CreateDate", "Keys:CreationDate")  # its clock (the date, see dates.py); local
GPS = ("GPS:GPSLatitude", "GPS:GPSLatitudeRef", "GPS:GPSLongitude", "GPS:GPSLongitudeRef")
ORIENTATION = ("IFD0:Orientation", "QuickTime:Rotation")
NUMERIC = {"GPS:GPSLatitude", "GPS:GPSLongitude", "IFD0:Orientation", "QuickTime:Rotation"}
ALL = DESCRIPTION + KEYWORDS + PHOTO_DATES + VIDEO_DATES + GPS + ORIENTATION + ("File:FileType",)

# What cameras put in ImageDescription when nobody wrote one
_JUNK = re.compile(r"^(|[A-Z ]*DIGITAL CAMERA|SONY DSC|DCIM|default|Untitled|Image)$", re.I)


class MetadataError(OSError):
    pass


# --- orientation -----------------------------------------------------------------------------------
# Each EXIF orientation maps a point of the stored image (u, v, from 0 to 1) to where it's shown:
# shown = M @ (u, v) + t.
_MAPS = {
    1: ((1, 0, 0, 1), (0, 0)),
    2: ((-1, 0, 0, 1), (1, 0)),
    3: ((-1, 0, 0, -1), (1, 1)),
    4: ((1, 0, 0, -1), (0, 1)),
    5: ((0, 1, 1, 0), (0, 0)),
    6: ((0, -1, 1, 0), (1, 0)),
    7: ((0, -1, -1, 0), (1, 1)),
    8: ((0, 1, -1, 0), (0, 1)),
}
# Turning what's shown: shown' = M @ shown + t
TURNS = {
    "right": ((0, -1, 1, 0), (1, 0)),
    "left": ((0, 1, -1, 0), (0, 1)),
    "flip-h": ((-1, 0, 0, 1), (1, 0)),
    "flip-v": ((1, 0, 0, -1), (0, 1)),
}
HEIC_ROTATION = {1: 0, 8: 90, 3: 180, 6: 270}  # degrees counter-clockwise


def _quarter_turns() -> bool:
    """Does ExifTool give QuickTime:Rotation (HEIC's rotation box) in quarter turns (0-3)? It has
    since 12.85; before, in degrees."""
    return exiftool().version() >= 12.85


def heic_degrees(raw) -> int:
    return int(raw or 0) * (90 if _quarter_turns() else 1) % 360


def heic_raw(degrees: int) -> int:
    return degrees // 90 if _quarter_turns() else degrees


def _compose(a, b):
    """The map doing b, then a."""
    (a11, a12, a21, a22), (at1, at2) = a
    (b11, b12, b21, b22), (bt1, bt2) = b
    m = (a11 * b11 + a12 * b21, a11 * b12 + a12 * b22, a21 * b11 + a22 * b21, a21 * b12 + a22 * b22)
    return m, (a11 * bt1 + a12 * bt2 + at1, a21 * bt1 + a22 * bt2 + at2)


def turned(orientation: int, turn: str) -> int:
    """The orientation after turning (or flipping) what's shown."""
    want = _compose(TURNS[turn], _MAPS.get(orientation, _MAPS[1]))
    return next(o for o, m in _MAPS.items() if m == want)


def move_box(box, old: int, new: int):
    """A box (x, y, w, h, as parts of the shown photo) moved to where it is once the photo is shown
    with orientation new instead of old."""
    (m11, m12, m21, m22), (t1, t2) = _MAPS.get(old, _MAPS[1])
    inverse = ((m11, m21, m12, m22), (-(m11 * t1 + m21 * t2), -(m12 * t1 + m22 * t2)))  # M is orthogonal
    (n11, n12, n21, n22), (s1, s2) = _compose(_MAPS.get(new, _MAPS[1]), inverse)
    x, y, w, h = box
    corners = [(n11 * u + n12 * v + s1, n21 * u + n22 * v + s2) for u, v in ((x, y), (x + w, y + h))]
    xs, ys = sorted(c[0] for c in corners), sorted(c[1] for c in corners)
    return xs[0], ys[0], xs[1] - xs[0], ys[1] - ys[0]


# --- reading ---------------------------------------------------------------------------------------


@dataclass
class Details:
    description: str = ""
    keywords: list = field(default_factory=list)
    taken: dt.datetime | None = None  # the date written in it (for a video, its clock, as stored)
    gps: tuple | None = None  # (latitude, longitude)
    orientation: int = 1
    kind: str = ""  # ExifTool's file type: JPEG, PNG, HEIC, DNG, MOV, ...
    tags: dict = field(default_factory=dict)  # the tags above that the file has, as ExifTool gives them


def _listed(v) -> list:
    if v is None or v == "":
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def details(path, tags: dict) -> Details:
    d = Details(kind=tags.get("File:FileType", ""), tags={k: v for k, v in tags.items() if k in ALL})
    for t in DESCRIPTION:
        text = str(tags.get(t) or "").strip()
        if text and not (t == "IFD0:ImageDescription" and _JUNK.match(text)):
            d.description = text
            break
    for t in KEYWORDS:
        if tags.get(t):
            d.keywords = _listed(tags[t])
            break
    if is_video(path):
        d.taken = parse(tags.get("QuickTime:CreateDate"), any_year=True)
    else:
        d.taken = next((x for x in (parse(tags.get(t), any_year=True) for t in PHOTO_DATES) if x), None)
    try:
        lat, lon = float(tags["GPS:GPSLatitude"]), float(tags["GPS:GPSLongitude"])
        lat = -lat if str(tags.get("GPS:GPSLatitudeRef", "N")).upper().startswith("S") else lat
        lon = -lon if str(tags.get("GPS:GPSLongitudeRef", "E")).upper().startswith("W") else lon
        if lat or lon:
            d.gps = (round(lat, 7), round(lon, 7))
    except (KeyError, TypeError, ValueError):
        pass
    if d.kind == "HEIC" and "QuickTime:Rotation" in tags:
        rot = {v: k for k, v in HEIC_ROTATION.items()}
        d.orientation = rot.get(heic_degrees(tags["QuickTime:Rotation"]), 1)
    else:
        try:
            d.orientation = int(tags.get("IFD0:Orientation") or 1)
        except (TypeError, ValueError):
            d.orientation = 1
    return d


def read(paths) -> dict[str, Details]:
    """{path: Details} for the files ExifTool can read."""
    found = exiftool().read(paths, ALL)
    return {p: details(p, t) for p, t in found.items()}


def read_all(path) -> dict:
    """Every tag in the file, as ExifTool shows them: {"Group:Name": "value"} (binary data summarised)."""
    found = exiftool().read([path], options=("-a", "-s"))
    return next(iter(found.values()), {})


# --- changing --------------------------------------------------------------------------------------


@dataclass
class Edit:
    """What to change; None means leave it as it is."""

    description: str | None = None
    keywords: list | None = None  # the whole list
    add_keywords: tuple = ()
    remove_keywords: tuple = ()
    date: dt.datetime | None = None  # set to this (local time)
    shift: dt.timedelta | None = None  # or move by this
    gps: tuple | None = None  # (lat, lon), or () to remove it
    turn: str | None = None  # "right", "left", "flip-h", "flip-v"

    def touches_date(self) -> bool:
        return self.date is not None or bool(self.shift)


def _fmt(d: dt.datetime) -> str:
    return d.strftime("%Y:%m:%d %H:%M:%S")


def _moved_date(value, edit: Edit):
    """A date tag's new value: the date set (or the old one shifted), keeping any offset it had."""
    text = str(value or "")
    old = parse(text, any_year=True)
    if edit.date is not None:
        new = edit.date
    elif old is not None:
        new = old + edit.shift
    else:
        return None
    rest = re.sub(r"^\d{4}:\d\d:\d\d[ T]\d\d:\d\d:\d\d", "", text) if old else ""
    if edit.date is not None:
        rest = re.sub(r"^\.\d+", "", rest)  # a new time has no fraction of a second
    return _fmt(new) + rest


class CantChange(ValueError):
    """This file can't take this change (a reason to show)."""


def changes(path, d: Details, edit: Edit, shown: dt.datetime | None = None) -> dict:
    """{tag: new value} to make edit to the file with details d (None: remove the tag). shown: the
    date the app shows for it, when that's a real one (it may come from the name): what moving the
    date moves when the file holds none itself."""
    has, out = d.tags, {}

    def also(names, value):  # the first is always written; the others only where the file has them
        out[names[0]] = value
        for t in names[1:]:
            if t in has:
                out[t] = value

    video = is_video(path)
    if edit.description is not None and edit.description.strip() != d.description:
        text = edit.description.strip() or None
        also(DESCRIPTION, text)
        if "IFD0:ImageDescription" in has and _JUNK.match(str(has["IFD0:ImageDescription"]).strip()) and text is None:
            out.pop("IFD0:ImageDescription", None)  # leave the camera's own words alone
    words = None
    if edit.keywords is not None:
        words = _unique(edit.keywords)
    elif edit.add_keywords or edit.remove_keywords:
        gone = {w.casefold() for w in edit.remove_keywords}
        words = _unique([w for w in d.keywords if w.casefold() not in gone] + list(edit.add_keywords))
    if words is not None and words != d.keywords:
        also(KEYWORDS, words or None)
    if edit.touches_date():
        out.update(_date_changes(has, d, edit, shown, video))
    if edit.gps is not None and not video and tuple(edit.gps or ()) != tuple(d.gps or ()):
        if edit.gps:
            lat, lon = edit.gps
            out.update(
                {
                    "GPS:GPSLatitude": abs(lat),
                    "GPS:GPSLatitudeRef": "N" if lat >= 0 else "S",
                    "GPS:GPSLongitude": abs(lon),
                    "GPS:GPSLongitudeRef": "E" if lon >= 0 else "W",
                }
            )
        else:
            out.update({t: None for t in GPS if t in has})
    if edit.turn:
        out.update(_turn_changes(has, d, edit.turn, video))
    return out


def _date_changes(has, d: Details, edit: Edit, shown, video: bool) -> dict:
    out = {}
    if video:
        # The clock gets the date as it's shown (dates.py), so setting one shows that one.
        for t in VIDEO_DATES:
            if t in has or t == "QuickTime:CreateDate":
                value = has.get(t) or (_fmt(shown) if t == "QuickTime:CreateDate" and shown else None)
                if t == "QuickTime:CreateDate" and edit.date is None and not parse(value, any_year=True):
                    continue
                new = _moved_date(value, edit)
                if new:
                    out[t] = new
        return out
    base = d.taken or shown
    if edit.date is None and base is None:
        raise CantChange("it has no date to move")
    for t in PHOTO_DATES:
        if t == PHOTO_DATES[0] or t in has:
            new = _moved_date(has.get(t) if t in has else (_fmt(base) if base else ""), edit)
            if new:
                out[t] = new
    return out


def _turn_changes(has, d: Details, turn: str, video: bool) -> dict:
    if video:
        raise CantChange("videos can't be turned here (your video player can)")
    new = turned(d.orientation, turn)
    if d.kind != "HEIC":
        return {"IFD0:Orientation": new}
    if new not in HEIC_ROTATION:
        raise CantChange("HEIC photos can be turned, but not flipped")
    if "QuickTime:Rotation" not in has:
        raise CantChange("this HEIC photo can't be turned")
    out = {"QuickTime:Rotation": heic_raw(HEIC_ROTATION[new])}
    if "IFD0:Orientation" in has:
        out["IFD0:Orientation"] = new
    return out


def _unique(words) -> list:
    seen, out = set(), []
    for w in (str(w).strip() for w in words):
        if w and w.casefold() not in seen:
            seen.add(w.casefold())
            out.append(w)
    return out


def before(d: Details, tags) -> dict:
    """The current values of these tags (None where the file doesn't have one), to undo a change."""
    return {t: d.tags.get(t) for t in tags}


def write(path, values: dict):
    """Write {tag: value} into the file (None removes the tag), keeping its modified time. Raises
    MetadataError with ExifTool's reason if it can't."""
    args = ["-m", "-P", "-overwrite_original"]
    for t, v in values.items():
        if v is None or v == []:
            args.append(f"-{t}=")
        elif isinstance(v, list):
            args += [f"-{t}={x}" for x in v]
        elif t in NUMERIC:
            args.append(f"-{t}#={v}")
        else:
            args.append(f"-{t}={v}")
    args.append(os.path.abspath(path))
    out, err = exiftool().run(args)
    text = out.decode("utf-8", "replace")
    if "1 image files updated" in text or "1 image files unchanged" in text:
        return
    reason = next((line for line in err.splitlines() if line.startswith("Error")), err.strip() or text.strip())
    reason = re.sub(r"^Error: (\[minor\] )?", "", reason)
    reason = reason.split(" - " + os.path.abspath(str(path)))[0]
    raise MetadataError(reason or "ExifTool couldn't change it")


def leftover(path) -> str:
    """The temporary file ExifTool writes beside a file while changing it."""
    return f"{path}_exiftool_tmp"


def written(path, values: dict) -> bool:
    """Does the file hold these values (so a write finished)?"""
    now = read([path]).get(os.path.abspath(str(path)))
    if now is None:
        return False
    for t, v in values.items():
        have = now.tags.get(t)
        if v is None or v == []:
            if have not in (None, "", []):
                return False
        elif isinstance(v, list):
            if _listed(have) != [str(x) for x in v]:
                return False
        elif t in NUMERIC:
            try:
                if abs(float(have) - float(v)) > 1e-6:
                    return False
            except (TypeError, ValueError):
                return False
        elif str(have) != str(v):
            return False
    return True
