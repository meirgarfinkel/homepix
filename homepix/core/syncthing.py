"""Setting up Syncthing for an import automation: talking to the Syncthing running on this computer
(only ever on this computer, through its own control address and key, read from its settings file),
to show this computer's device ID, connect a phone that asks to, and accept the folder the phone
shares as Receive Only, so photos arrive in an Import from folder and deleting them here never
deletes them on the phone.

HomePix itself still never goes on the network: Syncthing does the syncing.
"""

import ipaddress
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

TIMEOUT = 5


class NotRunning(Exception):
    """Syncthing isn't installed, or isn't running (the message says which, and what to do)."""


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
        raise NotRunning("Syncthing isn't installed on this computer.")
    base, key = read_config(found[0])
    st = Syncthing(base, key)
    try:
        st.my_id()
    except urllib.error.HTTPError as e:
        raise NotRunning(f"Syncthing didn't let HomePix in ({e.code}).") from e
    except (OSError, ValueError) as e:
        raise NotRunning("Syncthing is installed but isn't running.") from e
    return st
