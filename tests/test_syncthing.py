"""Setting up Syncthing: finding the one on this computer, and through its control API, pairing a
phone and taking the folder it shares as Receive Only. A stand-in Syncthing answers here, the way
the real one's REST API does."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from homepix.core import syncthing as stmod

MY_ID = "AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-HHHHHHH"
PHONE = "PPPPPPP-PPPPPPP-PPPPPPP-PPPPPPP-PPPPPPP-PPPPPPP-PPPPPPP-PPPPPPP"
KEY = "secret-key"


class FakeSyncthing:
    """The state a real Syncthing keeps, as far as setting up needs it."""

    def __init__(self):
        self.devices = {MY_ID: {"deviceID": MY_ID, "name": "this computer"}}
        self.folders = {}
        self.pending_devices = {}
        self.pending_folders = {}

    def handle(self, method, path, body):
        if path == "/rest/system/status":
            return {"myID": MY_ID}
        if path == "/rest/config/devices":
            return list(self.devices.values())
        if path == "/rest/cluster/pending/devices":
            return self.pending_devices
        if path == "/rest/config/defaults/device":
            return {"deviceID": "", "name": "", "addresses": ["dynamic"], "autoAcceptFolders": False}
        if path.startswith("/rest/config/devices/") and method == "PUT":
            self.devices[body["deviceID"]] = body
            self.pending_devices.pop(body["deviceID"], None)
            return None
        if path == "/rest/config/folders":
            return list(self.folders.values())
        if path == "/rest/cluster/pending/folders":
            return self.pending_folders
        if path == "/rest/config/defaults/folder":
            return {"id": "", "label": "", "path": "", "type": "sendreceive", "devices": [], "rescanIntervalS": 3600}
        if path.startswith("/rest/config/folders/") and method == "PUT":
            self.folders[body["id"]] = body
            self.pending_folders.pop(body["id"], None)
            return None
        raise KeyError(path)


def serve(state: FakeSyncthing) -> HTTPServer:
    """Answer as Syncthing's REST API would, on this computer, until shut down."""

    class Handler(BaseHTTPRequestHandler):
        def _go(self, method):
            if self.headers.get("X-API-Key") != KEY:
                self.send_response(403)
                self.end_headers()
                return
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n)) if n else None
            try:
                out = state.handle(method, self.path, body)
            except KeyError:
                self.send_response(404)
                self.end_headers()
                return
            data = b"" if out is None else json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._go("GET")

        def do_PUT(self):
            self._go("PUT")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    return server


@pytest.fixture
def fake():
    state = FakeSyncthing()
    server = serve(state)
    yield state
    server.shutdown()


def write_config(tmp_path, address, tls=False, key=KEY):
    folder = tmp_path / "state" / "syncthing"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.xml").write_text(
        f'<configuration version="37"><gui enabled="true" tls="{str(tls).lower()}">'
        f"<address>{address}</address><apikey>{key}</apikey></gui></configuration>"
    )
    return folder / "config.xml"


def test_reads_where_to_reach_it_and_only_ever_on_this_computer(tmp_path):
    assert stmod.read_config(write_config(tmp_path, "127.0.0.1:8384")) == ("http://127.0.0.1:8384", KEY)
    assert stmod.read_config(write_config(tmp_path, "0.0.0.0:8384", tls=True))[0] == "https://127.0.0.1:8384"
    assert stmod.read_config(write_config(tmp_path, "192.168.1.5:9000"))[0] == "http://127.0.0.1:9000"
    assert stmod.read_config(write_config(tmp_path, "[::1]:8384"))[0] == "http://[::1]:8384"
    assert stmod.read_config(write_config(tmp_path, "localhost:8384"))[0] == "http://localhost:8384"


def test_not_installed_and_not_running_say_so(tmp_path, monkeypatch):
    monkeypatch.setattr(stmod, "config_files", lambda: [tmp_path / "nope.xml"])
    monkeypatch.setattr(stmod, "find_program", lambda: None)
    with pytest.raises(stmod.NotRunning, match="isn't installed"):
        stmod.connect()
    path = write_config(tmp_path, "127.0.0.1:1")  # nothing listens there
    monkeypatch.setattr(stmod, "config_files", lambda: [path])
    with pytest.raises(stmod.NotRunning, match="isn't running"):
        stmod.connect()


def test_a_wrong_key_is_refused(fake, tmp_path, monkeypatch):
    path = write_config(tmp_path, fake.url.split("//")[1], key="wrong")
    monkeypatch.setattr(stmod, "config_files", lambda: [path])
    with pytest.raises(stmod.NotRunning, match="didn't let HomePix in"):
        stmod.connect()


def test_pairs_a_phone_and_takes_its_folder_as_receive_only(fake, tmp_path, monkeypatch):
    path = write_config(tmp_path, fake.url.split("//")[1])
    monkeypatch.setattr(stmod, "config_files", lambda: [path])
    st = stmod.connect()
    assert st.my_id() == MY_ID
    assert st.pending_devices() == {}
    fake.pending_devices[PHONE] = {"name": "Pixel 8", "address": "192.168.1.20:22000"}
    assert st.pending_devices() == {PHONE: "Pixel 8"}
    st.add_device(PHONE, "Pixel 8")
    assert st.devices()[PHONE] == "Pixel 8" and st.pending_devices() == {}
    assert fake.devices[PHONE]["addresses"] == ["dynamic"]  # (Syncthing's own defaults kept)

    fake.pending_folders["abcd-1234"] = {"offeredBy": {PHONE: {"label": "Camera"}}}
    fake.pending_folders["other"] = {"offeredBy": {"SOMEONE-ELSE": {"label": "Theirs"}}}
    assert st.pending_folders(PHONE) == {"abcd-1234": "Camera"}
    inbox = tmp_path / "Phone photos" / "Pixel 8"
    st.accept_folder("abcd-1234", "Camera", str(inbox), PHONE)
    f = fake.folders["abcd-1234"]
    assert f["type"] == "receiveonly" and f["path"] == str(inbox) and f["rescanIntervalS"] == 3600
    assert {d["deviceID"] for d in f["devices"]} == {PHONE, MY_ID}
    assert inbox.is_dir() and os.listdir(inbox) == []


def test_the_setup_window_pairs_and_fills_in_the_import_from_folder(app, fake, tmp_path, monkeypatch):
    from conftest import wait_until

    from homepix.ui.views import syncthing_dialog as sd

    path = write_config(tmp_path, fake.url.split("//")[1])
    monkeypatch.setattr(stmod, "config_files", lambda: [path])
    monkeypatch.setattr(sd, "POLL_MS", 50)
    inbox = tmp_path / "Inbox"
    d = sd.SyncthingDialog(str(inbox))
    d.timer.setInterval(50)
    d.show()
    try:
        wait_until(app, lambda: d.pairing.isVisible())
        assert d.device_id.text() == MY_ID and not d.qr.pixmap().isNull()
        assert not d.use.isEnabled()
        fake.pending_devices[PHONE] = {"name": "Pixel 8"}
        wait_until(app, lambda: d.found.isVisible())
        assert '"Pixel 8" wants to connect' in d.found_label.text()
        d.connect_button.click()
        wait_until(app, lambda: d.phone is not None)
        assert PHONE in fake.devices
        fake.pending_folders["cam"] = {"offeredBy": {PHONE: {"label": "Camera"}}}
        wait_until(app, lambda: d.use.isEnabled())
        assert d.folder == str(inbox) and d.phone_name == "Pixel 8"
        assert fake.folders["cam"]["type"] == "receiveonly" and inbox.is_dir()
    finally:
        d.close()
        d.deleteLater()
        app.processEvents()


def test_without_syncthing_install_sets_it_up_and_pairing_starts(app, fake, tmp_path, monkeypatch):
    from conftest import wait_until

    from homepix.ui.views import syncthing_dialog as sd

    config = tmp_path / "state" / "syncthing" / "config.xml"
    monkeypatch.setattr(stmod, "config_files", lambda: [config])
    monkeypatch.setattr(stmod, "find_program", lambda: None)
    monkeypatch.setattr(stmod, "install_command", lambda: ["pkexec", "apt-get", "install", "-y", "syncthing"])
    ran = []

    def install(env=None):  # (as if the installer ran and Syncthing started, writing its settings)
        ran.append(env is not None)
        write_config(tmp_path, fake.url.split("//")[1])
        return ""

    monkeypatch.setattr(stmod, "install", install)
    d = sd.SyncthingDialog()
    d.show()
    try:
        wait_until(app, lambda: d.setup.isVisible())
        assert "isn't installed" in d.status.text()
        assert d.install_button.isVisible() and d.install_button.text() == "Install Syncthing"
        assert d.install_button.isDefault() and not d.use.isDefault()
        assert "syncthing" in d.by_hand.text.text()  # (the same by hand, to copy into a terminal)
        d.install_button.click()
        wait_until(app, lambda: d.pairing.isVisible())
        assert ran == [True] and d.device_id.text() == MY_ID and not d.setup.isVisible()
        d.id_row.button.click()
        assert app.clipboard().text() == MY_ID and d.id_row.button.text() == "Copied"
    finally:
        d.close()
        d.deleteLater()
        app.processEvents()


def test_installed_but_stopped_offers_start(app, tmp_path, monkeypatch):
    from conftest import wait_until

    from homepix.ui.views import syncthing_dialog as sd

    path = write_config(tmp_path, "127.0.0.1:1")  # nothing listens there
    monkeypatch.setattr(stmod, "config_files", lambda: [path])
    monkeypatch.setattr(sd, "WAIT_S", 0)
    monkeypatch.setattr(stmod, "start", lambda env=None: "Unit syncthing.service not found.")
    d = sd.SyncthingDialog()
    d.show()
    try:
        wait_until(app, lambda: d.setup.isVisible())
        assert "isn't running" in d.status.text() and d.install_button.text() == "Start Syncthing"
        d.install_button.click()
        wait_until(app, lambda: "didn't work" in d.working.text())
        assert "Unit syncthing.service not found." in d.working.text() and d.install_button.isEnabled()
    finally:
        d.close()
        d.deleteLater()
        app.processEvents()


def test_with_no_installer_it_points_to_the_download_page(app, tmp_path, monkeypatch):
    from conftest import wait_until

    from homepix.ui.views import syncthing_dialog as sd

    monkeypatch.setattr(stmod, "config_files", lambda: [tmp_path / "none.xml"])
    monkeypatch.setattr(stmod, "find_program", lambda: None)
    monkeypatch.setattr(stmod, "install_command", lambda: None)
    d = sd.SyncthingDialog()
    d.show()
    try:
        wait_until(app, lambda: d.setup.isVisible())
        assert not d.install_button.isVisible() and d.download_button.isVisible()
        assert "download page" in d.status.text()
    finally:
        d.close()
        d.deleteLater()
        app.processEvents()


def _which(available):
    return lambda name: f"/usr/bin/{name}" if name in available else None


def test_install_uses_the_systems_own_installer(monkeypatch):
    monkeypatch.setattr(stmod.sys, "platform", "linux")
    monkeypatch.setattr(stmod.shutil, "which", _which({"pkexec", "apt-get"}))
    assert stmod.install_command() == ["/usr/bin/pkexec", "/usr/bin/apt-get", "install", "-y", "syncthing"]
    assert stmod.manual_commands().splitlines() == [
        "sudo apt install syncthing",
        "systemctl --user enable --now syncthing",
    ]
    monkeypatch.setattr(stmod.shutil, "which", _which({"pkexec", "dnf"}))
    assert stmod.install_command()[1:] == ["/usr/bin/dnf", "install", "-y", "syncthing"]
    assert stmod.manual_commands().startswith("sudo dnf install syncthing")
    monkeypatch.setattr(stmod.shutil, "which", _which({"apt-get"}))  # (no way to ask for the password)
    assert stmod.install_command() is None
    monkeypatch.setattr(stmod.sys, "platform", "win32")
    monkeypatch.setattr(stmod.shutil, "which", _which({"winget"}))
    cmd = stmod.install_command()
    assert cmd[:5] == ["/usr/bin/winget", "install", "--exact", "--id", "Syncthing.Syncthing"] and "--silent" in cmd
    monkeypatch.setattr(stmod.shutil, "which", _which(set()))
    assert stmod.install_command() is None


def test_start_on_linux_uses_its_user_service_else_starts_it_and_at_login(tmp_path, monkeypatch):
    monkeypatch.setattr(stmod.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(stmod, "find_program", lambda: "/usr/bin/syncthing")
    ran, started = [], []
    monkeypatch.setattr(stmod, "_start_detached", lambda args, env=None: started.append(args))
    monkeypatch.setattr(stmod.shutil, "which", _which({"systemctl"}))
    monkeypatch.setattr(stmod, "_run", lambda args, env=None, timeout=0: ran.append(args) or "")
    assert stmod.start() == ""
    assert ran == [["/usr/bin/systemctl", "--user", "enable", "--now", "syncthing.service"]] and started == []
    monkeypatch.setattr(stmod, "_run", lambda args, env=None, timeout=0: "no such unit")
    assert stmod.start() == ""
    assert started == [["/usr/bin/syncthing", "--no-browser"]]
    assert "Exec=/usr/bin/syncthing --no-browser" in (tmp_path / "autostart" / "syncthing.desktop").read_text()


def test_start_on_windows_starts_it_now_and_at_each_login(tmp_path, monkeypatch):
    monkeypatch.setattr(stmod.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    exe = r"C:\Users\me\AppData\Local\Microsoft\WinGet\Links\syncthing.exe"
    monkeypatch.setattr(stmod, "find_program", lambda: exe)
    started = []
    monkeypatch.setattr(stmod, "_start_detached", lambda args, env=None: started.append(args))
    assert stmod.start() == ""
    assert started == [[exe, "--no-browser"]]
    script = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Syncthing.vbs"
    assert script.read_text(encoding="utf-8").startswith(
        f'CreateObject("WScript.Shell").Run """{exe}"" --no-browser", 0'
    )


def test_never_run_yet_counts_as_installed_but_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(stmod, "config_files", lambda: [tmp_path / "nope.xml"])
    monkeypatch.setattr(stmod, "find_program", lambda: "/usr/bin/syncthing")
    with pytest.raises(stmod.NotRunning, match="isn't running") as e:
        stmod.connect()
    assert e.value.kind == stmod.STOPPED


def test_the_qr_code_holds_the_device_id(app):
    import cv2
    import numpy as np

    from homepix.ui.views.syncthing_dialog import qr_pixmap

    pm = qr_pixmap(MY_ID)
    assert pm.width() == pm.height() and pm.width() > 150
    img = pm.toImage().convertToFormat(pm.toImage().Format.Format_Grayscale8)
    pixels = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())[:, : img.width()]
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(np.ascontiguousarray(pixels))
    assert text == MY_ID  # what a phone's camera reads
