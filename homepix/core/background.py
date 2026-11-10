"""Keeping HomePix running in the background, so import automations work with the window closed: it
starts hidden at each login (with --background), and closing the window hides it instead of quitting.
On or off is kept in settings.json ("background"); the login entry is made or removed with it."""

import os
import plistlib
import sys
from pathlib import Path

from .settings import load_settings, save_settings

FLAG = "--background"  # started at login: no window until HomePix is opened from the menu
MAC_LABEL = "io.github.meirgarfinkel.homepix"


def on() -> bool:
    return bool(load_settings().get("background"))


def program() -> list[str]:
    """How to start this copy of HomePix: the AppImage, the installed program, or (from the source) Python."""
    if os.environ.get("APPIMAGE"):
        return [os.environ["APPIMAGE"]]
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "homepix.ui"]


def _linux_entry() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "homepix.desktop"


def _windows_entry() -> Path:
    startup = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return startup / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "HomePix.vbs"


def _mac_entry() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def entry() -> Path:
    """Where the login entry goes on this system."""
    if sys.platform == "win32":
        return _windows_entry()
    if sys.platform == "darwin":
        return _mac_entry()
    return _linux_entry()


def _quote(arg: str) -> str:
    return f'"{arg}"' if any(c in arg for c in ' "\\') else arg


def _entry_text(args: list[str]) -> bytes:
    if sys.platform == "win32":
        line = " ".join(f'""{a}""' for a in args)
        return f'CreateObject("WScript.Shell").Run "{line}", 0, False\r\n'.encode()
    if sys.platform == "darwin":
        bundle = Path(sys.executable).resolve().parents[2]  # HomePix.app/Contents/MacOS/HomePix
        if bundle.suffix == ".app":  # opened the Mac way, without taking the screen
            args = ["/usr/bin/open", "-g", "-a", str(bundle), "--args", FLAG]
        return plistlib.dumps({"Label": MAC_LABEL, "ProgramArguments": args, "RunAtLoad": True})
    return (
        "[Desktop Entry]\nType=Application\nName=HomePix\n"
        "Comment=Imports new photos in the background\n"
        f"Exec={' '.join(_quote(a) for a in args)}\nTerminal=false\nNoDisplay=true\n"
        "X-GNOME-Autostart-enabled=true\n"
    ).encode()


def set_on(want: bool) -> str:
    """Turn running in the background on or off: the setting, and the login entry. "" when it worked,
    or why the login entry couldn't be made (the setting is kept either way)."""
    s = load_settings()
    s["background"] = want
    save_settings(s)
    return refresh()


def refresh() -> str:
    """Make the login entry match the setting (and point at this copy, if HomePix moved)."""
    path = entry()
    try:
        if not on():
            path.unlink(missing_ok=True)
            return ""
        text = _entry_text(program() + [FLAG])
        if not path.exists() or path.read_bytes() != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text)
        return ""
    except OSError as e:
        return e.strerror or str(e)
