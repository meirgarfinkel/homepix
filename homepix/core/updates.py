"""Updates: is there a newer HomePix on GitHub, and getting the right file for how this copy was
installed. Checked at startup (installed copies only) and from the menu's Install updates."""

import hashlib
import json
import os
import sys
import urllib.request

from .. import __version__

LATEST = "https://api.github.com/repos/meirgarfinkel/homepix/releases/latest"
# the file each kind of install updates from
FILES = {
    "windows": "HomePixSetup.exe",
    "deb": "HomePix.deb",
    "appimage": "HomePix-x86_64.AppImage",
    "mac": "HomePix.dmg",
}


def how_installed() -> str | None:
    """'windows', 'deb', 'appimage', 'mac', or None (running from the source code)."""
    if not getattr(sys, "frozen", False):
        return None
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    if os.environ.get("APPIMAGE"):
        return "appimage"
    return "deb" if sys.executable.startswith("/opt/homepix/") else None


def version_of(tag: str) -> tuple:
    """'v1.2.3' -> (1, 2, 3); () if it isn't a version."""
    try:
        return tuple(int(p) for p in tag.lstrip("vV").split("."))
    except ValueError:
        return ()


def latest_release() -> dict:
    """The newest published release, as GitHub describes it."""
    req = urllib.request.Request(LATEST, headers={"Accept": "application/vnd.github+json", "User-Agent": "homepix"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def newer(release: dict, current: str = __version__) -> bool:
    return version_of(release.get("tag_name", "")) > version_of(current)


def file_for(release: dict, kind: str) -> dict | None:
    """The release's file for this kind of install (name, browser_download_url, size, digest)."""
    return next((a for a in release.get("assets", []) if a.get("name") == FILES.get(kind)), None)


def download(asset: dict, dest, progress=lambda done, total: None, should_stop=lambda: False) -> bool:
    """Download asset to dest (via a .part file, checked against the release's size and checksum).
    False if stopped."""
    tmp = f"{dest}.part"
    total, done, sha = asset.get("size") or 0, 0, hashlib.sha256()
    req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "homepix"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:
        while chunk := r.read(1 << 20):
            if should_stop():
                out.close()
                os.remove(tmp)
                return False
            out.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            progress(done, total)
    want = (asset.get("digest") or "").removeprefix("sha256:")
    if (total and done != total) or (want and sha.hexdigest() != want):
        os.remove(tmp)
        raise OSError("the download came through damaged. Please try again.")
    os.replace(tmp, dest)
    return True
