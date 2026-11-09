"""Setting up Syncthing for an import automation: talking to the Syncthing running on this computer
(only ever on this computer, through its own control address and key, read from its settings file),
to show this computer's device ID, connect a phone that asks to, and accept the folder the phone
shares as Receive Only, so photos arrive in an Import from folder and deleting them here never
deletes them on the phone.

HomePix itself still never goes on the network: Syncthing does the syncing. Install hands the
download to the system's own installer (apt, dnf or pacman; winget; Homebrew), and Start starts the
installed Syncthing and has it start by itself after that.
"""

import ipaddress
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

TIMEOUT = 5


class NotRunning(Exception):
    """Syncthing isn't installed, or isn't running (the message says which). kind: MISSING (Install
    fixes it), STOPPED (Start does), or "" (neither: it's running but something else is wrong)."""

    def __init__(self, message: str, kind: str = ""):
        super().__init__(message)
        self.kind = kind


MISSING, STOPPED = "missing", "stopped"


WINGET_ID = "Syncthing.Syncthing"


def _windows_programs() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    found = [
        local / "Microsoft" / "WinGet" / "Links" / "syncthing.exe",  # (winget; this app's PATH predates it)
        local / "Programs" / "Syncthing" / "syncthing.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Syncthing" / "syncthing.exe",
    ]
    return found + sorted((local / "Microsoft" / "WinGet" / "Packages").glob(f"{WINGET_ID}_*/**/syncthing.exe"))


def find_program() -> str | None:
    """The installed Syncthing (a program, or on macOS possibly its app), or None."""
    on_path = shutil.which("syncthing")
    if on_path:
        return on_path
    if sys.platform == "win32":
        places = _windows_programs()
    elif sys.platform == "darwin":
        places = [
            Path("/opt/homebrew/bin/syncthing"),
            Path("/usr/local/bin/syncthing"),
            Path("/Applications/Syncthing.app"),
        ]
    else:
        places = [Path("/usr/bin/syncthing"), Path.home() / ".local" / "bin" / "syncthing"]
    return next((str(p) for p in places if p.exists()), None)


def _brew() -> str | None:
    found = shutil.which("brew")
    return found or next((b for b in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if os.path.exists(b)), None)


def install_command() -> list[str] | None:
    """What Install runs: the system's own installer (it asks for the password itself), or None
    when there isn't one HomePix knows how to use."""
    if sys.platform == "win32":
        winget = shutil.which("winget")
        if winget:
            return [
                winget,
                "install",
                "--exact",
                "--id",
                WINGET_ID,
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ]
        return None
    if sys.platform == "darwin":
        brew = _brew()
        return [brew, "install", "syncthing"] if brew else None
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return None
    for tool, args in (("apt-get", ["install", "-y"]), ("dnf", ["install", "-y"]), ("pacman", ["-S", "--noconfirm"])):
        path = shutil.which(tool)
        if path:
            return [pkexec, path, *args, "syncthing"]
    return None


def manual_commands() -> str:
    """The same done by hand, to copy into a terminal."""
    if sys.platform == "win32":
        return f"winget install --exact --id {WINGET_ID}"
    if sys.platform == "darwin":
        return "brew install syncthing\nbrew services start syncthing"
    for tool, line in (("dnf", "sudo dnf install syncthing"), ("pacman", "sudo pacman -S syncthing")):
        if shutil.which(tool) and not shutil.which("apt-get"):
            return f"{line}\nsystemctl --user enable --now syncthing"
    return "sudo apt install syncthing\nsystemctl --user enable --now syncthing"


def _run(args, env=None, timeout=900) -> str:
    """Run a command and wait; its error output when it fails ("" when it worked)."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    if r.returncode == 0:
        return ""
    lines = [ln for ln in (r.stderr or r.stdout or "").strip().splitlines() if ln.strip()]
    return lines[-1] if lines else f"It stopped with code {r.returncode}."


def install(env=None) -> str:
    """Install Syncthing with the system's installer, then start it. "" when it worked, or why not.
    (pkexec says 126 or 127 when the password is cancelled.)"""
    cmd = install_command()
    if cmd is None:
        return "There's no installer here HomePix knows how to use."
    why = _run(cmd, env)
    if why:
        return why
    return start(env)


def _windows_start_at_login(program: str):
    """A small script in the Startup folder that starts Syncthing, with no window, at each login."""
    startup = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    startup = startup / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True, exist_ok=True)
    (startup / "Syncthing.vbs").write_text(
        f'CreateObject("WScript.Shell").Run """{program}"" --no-browser", 0, False\r\n', encoding="utf-8"
    )


def _linux_start_at_login(program: str):
    autostart = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"
    autostart.mkdir(parents=True, exist_ok=True)
    (autostart / "syncthing.desktop").write_text(
        f"[Desktop Entry]\nType=Application\nName=Syncthing\nExec={program} --no-browser\nNoDisplay=true\n"
    )


def _start_detached(args, env=None):
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(args, env=env, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(args, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start(env=None) -> str:
    """Start the installed Syncthing, and have it start by itself at each login from now on.
    "" when it started, or why not."""
    program = find_program()
    if program is None:
        return "Syncthing isn't installed."
    try:
        if sys.platform == "win32":
            _windows_start_at_login(program)
            _start_detached([program, "--no-browser"], env)
            return ""
        if sys.platform == "darwin":
            if program.endswith(".app"):
                return _run(["open", "-a", program], env)
            brew = _brew()
            if brew:
                return _run([brew, "services", "start", "syncthing"], env)
            _start_detached([program, "--no-browser"], env)
            return ""
        systemctl = shutil.which("systemctl")
        if systemctl and not _run([systemctl, "--user", "enable", "--now", "syncthing.service"], env, timeout=30):
            return ""
        _linux_start_at_login(program)  # (no systemd, or no unit for it)
        _start_detached([program, "--no-browser"], env)
        return ""
    except OSError as e:
        return str(e)


def config_files() -> list[Path]:
    """Where Syncthing keeps its settings (config.xml) on this system, most likely first."""
    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return [local / "Syncthing" / "config.xml"]
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Syncthing" / "config.xml"]
    state = Path(os.environ.get("XDG_STATE_HOME", home / ".local" / "state"))
    conf = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return [state / "syncthing" / "config.xml", conf / "syncthing" / "config.xml"]


def read_config(path: Path) -> tuple[str, str]:
    """(control address, key) from a config.xml. The address is always on this computer: one set to
    listen everywhere (0.0.0.0) is reached at 127.0.0.1."""
    gui = ET.parse(path).getroot().find("gui")
    if gui is None:
        raise NotRunning("Syncthing's settings don't say how to reach it.")
    key = (gui.findtext("apikey") or "").strip()
    address = (gui.findtext("address") or "127.0.0.1:8384").strip()
    host, _, port = address.rpartition(":")
    host = host.strip("[]")
    try:
        if not ipaddress.ip_address(host).is_loopback:
            host = "127.0.0.1"
    except ValueError:  # a name: only "localhost" is this computer
        if host != "localhost":
            host = "127.0.0.1"
    scheme = "https" if gui.get("tls", "false").lower() == "true" else "http"
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}", key


class Syncthing:
    def __init__(self, base: str, key: str):
        self.base, self.key = base.rstrip("/"), key

    def _call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("X-API-Key", self.key)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        ctx = None
        if self.base.startswith("https"):
            import ssl

            ctx = ssl.create_default_context()  # (its own certificate, on this computer only)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            text = r.read().decode() or "null"
        return json.loads(text)

    def my_id(self) -> str:
        return self._call("GET", "/rest/system/status")["myID"]

    def devices(self) -> dict[str, str]:
        """{device id: name} of the devices it's connected with (this computer too)."""
        return {d["deviceID"]: d.get("name") or d["deviceID"][:7] for d in self._call("GET", "/rest/config/devices")}

    def pending_devices(self) -> dict[str, str]:
        """{device id: name} of devices asking to connect."""
        return {
            k: v.get("name") or k[:7] for k, v in (self._call("GET", "/rest/cluster/pending/devices") or {}).items()
        }

    def add_device(self, device_id: str, name: str):
        d = self._call("GET", "/rest/config/defaults/device") | {"deviceID": device_id, "name": name}
        self._call("PUT", f"/rest/config/devices/{device_id}", d)

    def folders(self) -> dict[str, dict]:
        """{folder id: its settings}."""
        return {f["id"]: f for f in self._call("GET", "/rest/config/folders")}

    def pending_folders(self, device_id: str) -> dict[str, str]:
        """{folder id: label} of folders a device has offered to share and nobody has accepted yet."""
        out = {}
        for fid, info in (self._call("GET", "/rest/cluster/pending/folders") or {}).items():
            offer = (info.get("offeredBy") or {}).get(device_id)
            if offer is not None:
                out[fid] = offer.get("label") or fid
        return out

    def accept_folder(self, folder_id: str, label: str, path: str, device_id: str):
        """Take a folder the phone shares, into path, as Receive Only: what comes in is never sent
        back, so emptying it here doesn't touch the phone."""
        os.makedirs(path, exist_ok=True)
        f = self._call("GET", "/rest/config/defaults/folder") | {
            "id": folder_id,
            "label": label,
            "path": path,
            "type": "receiveonly",
            "devices": [{"deviceID": device_id}, {"deviceID": self.my_id()}],
        }
        self._call("PUT", f"/rest/config/folders/{folder_id}", f)


def connect() -> Syncthing:
    """The Syncthing running on this computer. Raises NotRunning with what to do if there isn't one."""
    found = [p for p in config_files() if p.is_file()]
    if not found:
        if find_program() is None:
            raise NotRunning("Syncthing isn't installed on this computer.", MISSING)
        raise NotRunning("Syncthing is installed but isn't running.", STOPPED)  # (never run yet: no settings)
    base, key = read_config(found[0])
    st = Syncthing(base, key)
    try:
        st.my_id()
    except urllib.error.HTTPError as e:
        raise NotRunning(f"Syncthing didn't let HomePix in ({e.code}).") from e
    except (OSError, ValueError) as e:
        raise NotRunning("Syncthing is installed but isn't running.", STOPPED) from e
    return st
