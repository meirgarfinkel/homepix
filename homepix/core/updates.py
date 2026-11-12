"""Updates: is there a newer HomePix on GitHub, and getting the right file for how this copy was
installed. Checked at startup (installed copies only) and from the menu's Check for updates."""

import hashlib
import json
import os
import re
import sys
import urllib.request

from .. import __version__

RELEASES = "https://api.github.com/repos/meirgarfinkel/homepix/releases?per_page=50"
DOWNLOADS = "https://github.com/meirgarfinkel/homepix/releases/download/"  # the only place updates come from
_DESKTOP_TAG = re.compile(r"^v\d+\.\d+\.\d+$")  # (the phone app's are android-v0.7)
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


def latest_release(kind: str | None = None) -> dict:
    """The newest desktop release (the highest vX.Y.Z, not what GitHub calls latest: a phone release
    marked so by mistake would stop updates), with the file for kind if given. {} if there's none."""
    req = urllib.request.Request(RELEASES, headers={"Accept": "application/vnd.github+json", "User-Agent": "homepix"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return newest(json.load(r), kind)


def newest(releases: list, kind: str | None = None) -> dict:
    ok = [
        r
        for r in releases
        if isinstance(r, dict)
        and not r.get("draft")
        and not r.get("prerelease")
        and _DESKTOP_TAG.match(r.get("tag_name", ""))
        and (kind is None or file_for(r, kind) is not None)
    ]
    return max(ok, key=lambda r: version_of(r["tag_name"]), default={})


def newer(release: dict, current: str = __version__) -> bool:
    return version_of(release.get("tag_name", "")) > version_of(current)


def file_for(release: dict, kind: str) -> dict | None:
    """The release's file for this kind of install (name, browser_download_url, size, digest)."""
    return next((a for a in release.get("assets", []) if a.get("name") == FILES.get(kind)), None)


def download(asset: dict, dest, progress=lambda done, total: None, should_stop=lambda: False) -> bool:
    """Download asset to dest (via a .part file, checked against the release's size and checksum; one
    already there that checks out is kept). Refused when the release gives no checksum, or the file
    isn't on HomePix's own GitHub releases. False if stopped."""
    url = asset.get("browser_download_url") or ""
    want = (asset.get("digest") or "").removeprefix("sha256:").lower()
    total = asset.get("size") or 0
    if not url.startswith(DOWNLOADS):
        raise OSError("the update isn't on HomePix's own releases, so it wasn't downloaded.")
    if len(want) != 64 or not total:
        raise OSError("the update has no checksum to check it by, so it wasn't downloaded.")
    if os.path.isfile(dest) and os.path.getsize(dest) == total and _sha256(dest) == want:
        progress(total, total)
        return True  # (downloaded before, and not installed yet)
    tmp = f"{dest}.part"
    done, sha = 0, hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "homepix"})
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
    if done != total or sha.hexdigest() != want:
        os.remove(tmp)
        raise OSError("the download came through damaged. Please try again.")
    os.replace(tmp, dest)
    return True


def _sha256(path) -> str:
    d = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            d.update(chunk)
    return d.hexdigest()
