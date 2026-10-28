"""The user's settings (which folders make up the library), kept in settings.json in the data folder.

Both apps and the CLI read the same file, so a folder added in one shows up in the others.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from .config import data_dir
from .db import open_db


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        s = json.loads(settings_path().read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {"folders": []}
    except Exception:
        return {"folders": []}


def save_settings(s: dict):
    tmp = settings_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    tmp.replace(settings_path())


def library_folders() -> list[str]:
    return list(load_settings().get("folders", []))


def excluded_folders() -> list[str]:
    """Folders left out of the library's views: their photos stay indexed, and show when the folder
    itself is opened, but not in All photos, someone's photos or search."""
    return list(load_settings().get("excluded", []))


def set_excluded(path: str, excluded: bool):
    """Leave a folder out (or take it back in). A folder inside one already left out needs nothing
    more; leaving out a folder that holds some already left out replaces them."""
    path = os.path.abspath(path)
    s = load_settings()
    current = [os.path.abspath(f) for f in s.get("excluded", [])]
    inside = os.path.join(path, "")
    if excluded:
        if not any(path == f or path.startswith(os.path.join(f, "")) for f in current):
            current = [f for f in current if not f.startswith(inside)] + [path]
    else:
        current = [f for f in current if f != path]
    s["excluded"] = current
    save_settings(s)


def add_folder(path) -> Path:
    """Add a folder to the library. Adding a folder that holds library folders replaces them.
    Raises ValueError, with a message to show, if it can't be added."""
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise ValueError("That folder doesn't exist.")
    if p == data_dir().resolve() or data_dir().resolve() in p.parents:
        raise ValueError("That's where Photo Finder keeps its own files.")
    s = load_settings()
    folders = s.setdefault("folders", [])
    if any(p == Path(f) or Path(f) in p.parents for f in folders):
        raise ValueError("That folder is already in your library.")
    s["folders"] = [f for f in folders if p not in Path(f).parents] + [str(p)]
    save_settings(s)
    return p


def remove_folder(path: str) -> int:
    """Take a folder out of the library and forget its photos (the files themselves aren't touched).
    Returns how many photos were forgotten."""
    s = load_settings()
    s["folders"] = [f for f in s.get("folders", []) if f != path]
    save_settings(s)
    prefix = path.rstrip(os.sep) + os.sep
    db = open_db()
    try:
        n = db.execute("DELETE FROM photos WHERE substr(path, 1, ?) = ?", (len(prefix), prefix)).rowcount
        db.commit()
        return n
    finally:
        db.close()


def pictures_folder() -> Path:
    """The user's Pictures folder, wherever their system keeps it."""
    if os.name != "nt" and shutil.which("xdg-user-dir"):
        try:
            out = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True, timeout=3).stdout.strip()
            if out and Path(out).is_dir() and Path(out) != Path.home():
                return Path(out)
        except Exception:
            pass
    return Path.home() / "Pictures"
