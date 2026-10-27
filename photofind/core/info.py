"""Everything the info panel shows about one photo: from the index, the file and its EXIF."""

import json
import os
from pathlib import Path

from PIL import ExifTags, Image

from .video import video_size

GPS_IFD = 0x8825
EXIF_IFD = 0x8769
DATE_SOURCES = {
    "exif": "written in the photo (EXIF)",
    "file-modified": "guessed from when the file was last changed",
    "video": "recorded in the video",
    "filename": "from the file's name",
    None: "unknown (indexed before this was recorded)",
}


def _deg(value, ref):
    """EXIF degrees/minutes/seconds to a signed decimal."""
    d, m, s = (float(x) for x in value)
    v = d + m / 60 + s / 3600
    return -v if ref in ("S", "W") else v


def read_exif(path: Path) -> dict:
    """Camera, lens, exposure, dimensions and GPS from the file. Missing values are simply left out."""
    out = {}
    with Image.open(path) as img:
        w, h = img.size
        exif = img.getexif()
        if exif.get(ExifTags.Base.Orientation) in (5, 6, 7, 8):  # shown rotated a quarter turn
            w, h = h, w
        out["dimensions"] = (w, h)
        sub = exif.get_ifd(EXIF_IFD)
        make, model = (str(exif.get(t) or "").strip("\x00 ") for t in (ExifTags.Base.Make, ExifTags.Base.Model))
        if model:
            out["camera"] = model if model.lower().startswith(make.lower()) else f"{make} {model}".strip()
        lens = str(sub.get(ExifTags.Base.LensModel) or "").strip("\x00 ")
        if lens:
            out["lens"] = lens
        parts = []
        if f := sub.get(ExifTags.Base.FNumber):
            parts.append(f"f/{float(f):g}")
        if t := sub.get(ExifTags.Base.ExposureTime):
            t = float(t)
            parts.append(f"1/{round(1 / t)} s" if 0 < t < 1 else f"{t:g} s")
        if iso := sub.get(ExifTags.Base.ISOSpeedRatings):
            parts.append(f"ISO {iso[0] if isinstance(iso, tuple) else iso}")
        if fl := sub.get(ExifTags.Base.FocalLength):
            parts.append(f"{float(fl):g} mm")
        if parts:
            out["exposure"] = " · ".join(parts)
        gps = exif.get_ifd(GPS_IFD)
        try:
            lat = _deg(gps[ExifTags.GPS.GPSLatitude], gps.get(ExifTags.GPS.GPSLatitudeRef))
            lon = _deg(gps[ExifTags.GPS.GPSLongitude], gps.get(ExifTags.GPS.GPSLongitudeRef))
            if lat or lon:  # 0,0 is what some phones write when they had no fix
                out["gps"] = (round(lat, 6), round(lon, 6))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            pass
    return out


def human_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000


def photo_info(db, photo_id: int) -> dict | None:
    """None if the photo isn't in the index. 'offline' is set when the file can't be reached right now."""
    r = db.execute("SELECT id, path, size, taken, taken_source, kind, duration, keywords, description "
                   "FROM photos WHERE id=?", (photo_id,)).fetchone()
    if r is None:
        return None
    path = Path(r["path"])
    info = {
        "id": r["id"],
        "path": str(path),
        "name": path.name,
        "folder": str(path.parent),
        "taken": r["taken"],
        "kind": r["kind"],
        "date_source": DATE_SOURCES.get(r["taken_source"], r["taken_source"]),
        "people": [x[0] for x in db.execute(
            "SELECT DISTINCT pe.name FROM faces f JOIN people pe ON pe.id = f.person_id WHERE f.photo_id=? "
            "ORDER BY pe.name", (photo_id,))],
    }
    if r["duration"]:
        info["duration"] = r["duration"]
    info["keywords"] = json.loads(r["keywords"]) if r["keywords"] else []
    info["description"] = r["description"] or ""
    try:
        info["size"] = human_size(os.stat(path).st_size)
        if r["kind"] == "photo":
            info.update(read_exif(path))
        elif dims := video_size(path):
            info["dimensions"] = dims
    except OSError:
        info["offline"] = True
        if r["size"]:
            info["size"] = human_size(r["size"])
    except Exception as e:  # a damaged file: show what the index knows
        info["error"] = str(e)
    return info
