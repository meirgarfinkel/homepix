"""Phones sending photos straight to HomePix: pairing with the QR code's one-time code, only paired
phones being answered, only what's needed being sent, an upload going on after it's cut off, and files
landing whole, in place, ready for the import automation."""

import base64
import hashlib
import http.client
import json
import os
import ssl
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import pytest
from conftest import photo

from homepix.core import autoimport as ai
from homepix.core import phone_server, phones


@pytest.fixture
def setup(home, lib, tmp_path):
    """A running server, an automation from an Inbox folder, and the computer's fingerprint."""
    _, db = home
    watch = tmp_path / "Inbox"
    watch.mkdir()
    rule = ai.Rule(name="Phone", watch=str(watch), dest=str(lib), tidy=True)
    ai.save_rules([rule])
    events = []
    server = phone_server.Server(lambda kind, phone: events.append((kind, phone.name)), port=0, announce=False)
    server.start()
    yield server, rule, watch, events, db
    server.stop()
    phones.close_pairing()


class Phone:
    """What the phone app does: an encrypted connection that trusts only the computer's fingerprint."""

    def __init__(self, port, fingerprint, token=""):
        self.port, self.fingerprint, self.token = port, fingerprint, token

    def call(self, method, path, body=b"", headers=None):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # (checked below, against the QR code's fingerprint)
        c = http.client.HTTPSConnection("127.0.0.1", self.port, context=ctx, timeout=10)
        c.connect()
        seen = hashlib.sha256(c.sock.getpeercert(binary_form=True)).hexdigest()
        assert seen == self.fingerprint
        h = {"Content-Length": str(len(body)), **(headers or {})}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        c.close()
        return out

    def post(self, path, obj):
        return self.call("POST", path, json.dumps(obj).encode(), {"Content-Type": "application/json"})

    def send(self, rel, data, offset=0, upto=None, sha=None, mtime_ms=1_700_000_000_000):
        chunk = data[offset:upto]
        return self.call(
            "PUT",
            "/v1/file",
            chunk,
            {
                "X-Path": quote(rel),
                "X-Size": str(len(data)),
                "X-Mtime": str(mtime_ms),
                "X-Sha256": sha or hashlib.sha256(data).hexdigest(),
                "X-Offset": str(offset),
            },
        )


def fingerprint(qr) -> str:
    return base64.urlsafe_b64decode(qr["f"][0] + "=").hex()


def paired(server, rule):
    p = phones.open_pairing(rule.id)
    qr = parse_qs(urlparse(phones.qr_text(p, server.port)).query)
    phone = Phone(server.port, fingerprint(qr))
    status, body = phone.post("/v1/pair", {"code": qr["c"][0], "name": "Pixel 7"})
    assert status == 200
    phone.token = body["token"]
    return phone


def file_info(rel, data):
    return {"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_pairing_gives_the_phone_a_secret_once(setup):
    server, rule, _, events, _ = setup
    p = phones.open_pairing(rule.id)
    qr = parse_qs(urlparse(phones.qr_text(p, server.port)).query)
    assert qr["p"] == [str(server.port)] and qr["c"] == [p.code]
    phone = Phone(server.port, fingerprint(qr))

    assert phone.post("/v1/pair", {"code": "wrong", "name": "x"})[0] == 403
    status, body = phone.post("/v1/pair", {"code": p.code, "name": "Pixel 7"})
    assert status == 200 and body["token"]
    assert [x.name for x in phones.load_phones()] == ["Pixel 7"]
    assert body["token"] not in phones._phones_file().read_text()  # (only its fingerprint is kept)
    assert ("paired", "Pixel 7") in events
    assert phone.post("/v1/pair", {"code": p.code, "name": "Again"})[0] == 403  # (once only)

    phone.token = body["token"]
    status, hello = phone.post("/v1/hello", {})
    assert status == 200 and hello["phone"] == "Pixel 7" and hello["ready"]


def test_pairing_closes_after_its_time(setup, monkeypatch):
    server, rule, _, _, _ = setup
    p = phones.open_pairing(rule.id)
    monkeypatch.setattr(p, "expires", time.time() - 1)
    phone = Phone(server.port, phones.identity().fingerprint)
    assert phone.post("/v1/pair", {"code": p.code, "name": "x"})[0] == 403


def test_only_paired_phones_are_answered(setup):
    server, rule, _, _, _ = setup
    stranger = Phone(server.port, phones.identity().fingerprint, token="made-up")
    assert stranger.post("/v1/hello", {})[0] == 401
    assert stranger.post("/v1/need", {"files": []})[0] == 401
    assert stranger.send("Camera/a.jpg", b"x")[0] == 401

    phone = paired(server, rule)
    phones.forget(phones.load_phones()[0].id)
    assert phone.post("/v1/hello", {})[0] == 401  # (unpaired: its secret stops working)


def test_the_computer_keeps_its_key():
    a = phones.identity()
    assert phones.identity().fingerprint == a.fingerprint
    if os.name != "nt":
        assert oct(a.key.stat().st_mode & 0o777) == "0o600"


def test_a_photo_arrives_whole_in_place_with_the_phones_time(setup, tmp_path):
    server, rule, watch, events, _ = setup
    phone = paired(server, rule)
    data = photo(tmp_path / "src.jpg").read_bytes()

    status, body = phone.post("/v1/need", {"files": [file_info("Camera/IMG_1.jpg", data)]})
    assert status == 200 and body["need"] == [{"path": "Camera/IMG_1.jpg", "have": 0}]
    assert phone.send("Camera/IMG_1.jpg", data) == (200, {"have": len(data)})

    got = watch / "Camera" / "IMG_1.jpg"
    assert got.read_bytes() == data
    assert int(got.stat().st_mtime) == 1_700_000_000
    assert ("arrived", "Pixel 7") in events
    assert list((watch / phones.INCOMING).iterdir()) == []
    # (here, waiting for the import: not needed again)
    assert phone.post("/v1/need", {"files": [file_info("Camera/IMG_1.jpg", data)]})[1]["need"] == []


def test_an_upload_cut_off_goes_on_from_where_it_stopped(setup, tmp_path):
    server, rule, watch, _, _ = setup
    phone = paired(server, rule)
    data = os.urandom(300_000)
    rel = "Camera/VID_1.mp4"

    assert phone.send(rel, data, 0, 100_000) == (200, {"have": 100_000})
    assert not (watch / "Camera" / "VID_1.mp4").exists()  # (half a file is never where the import looks)
    assert phone.post("/v1/need", {"files": [file_info(rel, data)]})[1]["need"] == [{"path": rel, "have": 100_000}]

    status, body = phone.send(rel, data, 0)  # (the phone thinks it's starting over)
    assert status == 409 and body["have"] == 100_000
    assert phone.send(rel, data, 100_000) == (200, {"have": len(data)})
    assert (watch / "Camera" / "VID_1.mp4").read_bytes() == data


def test_a_file_that_arrives_wrong_is_thrown_away(setup):
    server, rule, watch, _, _ = setup
    phone = paired(server, rule)
    data = os.urandom(1000)
    status, _ = phone.send("Camera/a.jpg", data, sha=hashlib.sha256(b"something else").hexdigest())
    assert status == 422
    assert not (watch / "Camera" / "a.jpg").exists()
    assert list((watch / phones.INCOMING).iterdir()) == []


def test_imported_content_is_never_sent_again(setup, lib, tmp_path):
    server, rule, watch, _, db = setup
    phone = paired(server, rule)
    data = photo(tmp_path / "src.jpg").read_bytes()
    assert phone.send("Camera/IMG_2.jpg", data)[0] == 200
    later = time.time() + ai.SETTLE + 5
    batch = ai.check(db, rule, [str(lib)], now=later)
    ai.run(db, batch)
    assert not (watch / "Camera" / "IMG_2.jpg").exists()  # (imported: the folder is emptied)

    # Asked again (after the app was reinstalled, say), at the same place or any other: not needed.
    files = [file_info("Camera/IMG_2.jpg", data), file_info("Camera/copy.jpg", data)]
    assert phone.post("/v1/need", {"files": files})[1]["need"] == []


def test_a_changed_photo_replaces_the_one_waiting(setup, tmp_path):
    server, rule, watch, _, _ = setup
    phone = paired(server, rule)
    a = photo(tmp_path / "a.jpg").read_bytes()
    b = photo(tmp_path / "b.jpg").read_bytes()
    assert phone.send("Camera/IMG_3.jpg", a)[0] == 200
    assert phone.post("/v1/need", {"files": [file_info("Camera/IMG_3.jpg", b)]})[1]["need"]
    assert phone.send("Camera/IMG_3.jpg", b)[0] == 200
    assert (watch / "Camera" / "IMG_3.jpg").read_bytes() == b


@pytest.mark.parametrize(
    "rel",
    [
        "../a.jpg",
        "Camera/../../a.jpg",
        "/etc/a.jpg",
        ".hidden/a.jpg",
        "Camera/.a.jpg",
        "a\\b.jpg",
        "a.exe",
        "",
        "CON.jpg",
    ],
)
def test_no_way_out_of_the_folder(setup, rel):
    server, rule, watch, _, _ = setup
    phone = paired(server, rule)
    assert phone.send(rel, b"data")[0] == 400
    assert phone.post("/v1/need", {"files": [file_info(rel, b"data")]})[1]["need"] == []


def test_a_paused_automation_takes_nothing(setup):
    server, rule, _, _, _ = setup
    phone = paired(server, rule)
    rule.enabled = False
    ai.save_rules([rule])
    assert phone.post("/v1/hello", {})[1]["ready"] is False
    assert phone.send("Camera/a.jpg", b"x")[0] == 409


def test_only_the_home_network_is_answered():
    for ok in ("192.168.1.5", "10.0.0.16", "172.16.4.1", "127.0.0.1", "::1", "fe80::1%wlan0", "::ffff:192.168.0.2"):
        assert phones.private(ok), ok
    for no in ("8.8.8.8", "1.1.1.1", "2001:4860::8888", "::ffff:8.8.8.8", "nonsense"):
        assert not phones.private(no), no


@pytest.fixture
def own_home(tmp_path, monkeypatch):
    """A home folder of the test's own (where "Phone photos" is made)."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


def test_settings_pairs_a_phone_into_its_own_automation(app, home, lib, tmp_path, own_home, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget

    from homepix.ui.phone_link import PhoneLink
    from homepix.ui.views import pair_dialog
    from homepix.ui.views.settings_dialog import SettingsDialog

    (tmp_path / "Inbox").mkdir()
    old = ai.Rule(name="Meirs phone", watch=str(tmp_path / "Inbox"), dest=str(lib), tidy=True)
    ai.save_rules([old])
    window = QWidget()
    window.phone_link = PhoneLink(window)
    monkeypatch.setattr(phone_server.Server, "_announce", lambda self: None)  # (not on the real network)
    seen = {}

    def pair_and_look(dlg):
        seen["choices"] = [dlg.rule.itemText(i) for i in range(dlg.rule.count())]
        seen["qr"] = not dlg.qr.pixmap().isNull()
        seen["listening"] = window.phone_link.server is not None
        window.phone_link._event("paired", phones.pair(phones.pairing().code, "Pixel 7")[0])
        app.processEvents()
        seen["status"] = dlg.status.text()
        return dlg.done(1)

    monkeypatch.setattr(pair_dialog.PairDialog, "exec", pair_and_look)
    dlg = SettingsDialog(window)
    assert dlg.phone_list.count() == 0 and not dlg.unpair_button.isEnabled()
    dlg._pair()
    assert seen["choices"] == ["Its own new import automation", "Meirs phone (an existing automation)"]
    assert seen["qr"] and seen["listening"] and "Pixel 7" in seen["status"]
    assert phones.pairing() is None  # (closed with the window)
    assert window.phone_link.server is not None  # (a phone is paired: still answering)

    rules = ai.load_rules()
    made = rules[1]
    assert made.name == "Pixel 7" and made.watch == str(own_home / "Phone photos" / "Pixel 7")
    assert os.path.isdir(made.watch) and made.tidy and made.dest == str(lib)
    assert ai.problem(made, rules, [str(lib)]) is None
    assert phones.load_phones()[0].rule_id == made.id
    assert dlg.phone_list.count() == 1 and "into Pixel 7" in dlg.phone_list.item(0).text()
    assert dlg.rule_list.count() == 2

    # A second phone isn't offered the first one's automation.
    dlg2 = pair_dialog.PairDialog(window.phone_link, rules, [str(lib)])
    assert [dlg2.rule.itemText(i) for i in range(dlg2.rule.count())] == [
        "Its own new import automation",
        "Meirs phone (an existing automation)",
    ]
    dlg2.done(0)

    # Unpairing takes away the automation made for it (nothing was waiting in its folder).
    dlg.phone_list.item(0).setSelected(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dlg._unpair()
    assert phones.load_phones() == [] and dlg.phone_list.count() == 0
    assert [r.name for r in ai.load_rules()] == ["Meirs phone"]
    assert window.phone_link.server is None  # (no phone left: not listening)


def test_each_phones_folder_is_its_own(home, lib, own_home):
    a, _ = phones.make_rule("Pixel 7", "a", [str(lib)], [])
    b, _ = phones.make_rule("Pixel 7", "b", [str(lib)], [a])  # (two phones with one name)
    assert a.watch != b.watch and b.watch.endswith("Pixel 7 2")
    odd, _ = phones.make_rule('Dad\'s "phone"/2', "c", [str(lib)], [a, b])
    assert os.path.basename(odd.watch) == "Dad's phone2"
    (own_home / "Phone photos" / "Mom").mkdir()
    (own_home / "Phone photos" / "Mom" / "x.jpg").write_bytes(b"x")  # (someone's files already there)
    mom, _ = phones.make_rule("Mom", "d", [str(lib)], [])
    assert mom.watch.endswith("Mom 2")


def test_a_phone_folder_inside_the_library_is_excluded(home, own_home):
    from homepix.core.settings import excluded_folders

    rule, excluded = phones.make_rule("Pixel 7", "a", [str(own_home)], [])  # (the home folder is the library)
    assert excluded and rule.watch in excluded_folders()


def test_two_phones_with_the_same_names_never_mix(setup, lib, tmp_path, own_home):
    server, rule_a, watch_a, _, db = setup
    rule_b, _ = phones.make_rule("Phone B", "rule-b", [str(lib)], [rule_a])
    ai.save_rules([rule_a, rule_b])
    a = paired(server, rule_a)
    b = paired(server, rule_b)
    mine = photo(tmp_path / "a.jpg").read_bytes()
    theirs = photo(tmp_path / "b.jpg").read_bytes()
    rel = "WhatsApp/IMG-20261111-WA0001.jpg"  # (WhatsApp's names repeat from one phone to the next)

    assert a.send(rel, mine)[0] == 200
    ai.run(db, ai.check(db, rule_a, [str(lib)], now=time.time() + ai.SETTLE + 5))
    assert b.send(rel, theirs)[0] == 200
    assert (Path(rule_b.watch) / rel).read_bytes() == theirs  # (in phone B's folder, not phone A's)
    batch = ai.check(db, rule_b, [str(lib)], now=time.time() + ai.SETTLE + 5)
    assert len(batch.imports) == 1 and not batch.updates  # (a new photo, not a new version of A's)
    ai.run(db, batch)
    from homepix.core.library import index_folder

    index_folder(lib, want_faces=False)
    contents = sorted(Path(p).read_bytes() for (p,) in db.execute("SELECT path FROM photos").fetchall())
    assert contents == sorted([mine, theirs])

    # Phone B's library file doesn't take phone A's photo for its own by name.
    data, _ = ask_library(b, [len(theirs) + 1], [rel])
    out = tmp_path / "b.db"
    out.write_bytes(data)
    photos_b, came_b, _ = library_rows(out)
    assert len(photos_b) == 1 and list(came_b.values()) == [rel]
    path_b = list(came_b)[0]
    assert Path(path_b.replace("/", os.sep)).read_bytes() == theirs


def library_rows(path):
    import sqlite3

    with sqlite3.connect(path) as c:
        photos = sorted(os.path.basename(r[0]) for r in c.execute("SELECT path FROM photos"))
        came = dict(c.execute("SELECT path, source FROM came_from").fetchall())
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return photos, came, tables


def ask_library(phone, sizes, sources=(), version=None):
    body = json.dumps({"sizes": list(sizes), "sources": list(sources), "version": version}).encode()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    c = http.client.HTTPSConnection("127.0.0.1", phone.port, context=ctx, timeout=30)
    c.request("POST", "/v1/library", body=body, headers={"Authorization": "Bearer " + phone.token})
    r = c.getresponse()
    data, version = r.read(), r.getheader("X-Version")
    c.close()
    assert r.status == 200
    return data, version


def test_the_phone_gets_only_its_own_photos_details(setup, lib, tmp_path):
    server, rule, watch, _, db = setup
    pf_index = __import__("homepix.core.library", fromlist=["index_folder"]).index_folder
    phone = paired(server, rule)
    # one photo sent from the phone and imported, two that never were on it
    sent = photo(tmp_path / "src.jpg").read_bytes()
    assert phone.send("Camera/PXL_1.jpg", sent)[0] == 200
    ai.run(db, ai.check(db, rule, [str(lib)], now=time.time() + ai.SETTLE + 5))
    photo(lib / "Other" / "a.jpg")
    photo(lib / "Other" / "bigger.jpg")
    pf_index(lib, want_faces=False)
    assert db.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 3

    data, version = ask_library(phone, [len(sent)], ["Camera/PXL_1.jpg"])
    out = tmp_path / "phone.db"
    out.write_bytes(data)
    photos, came, tables = library_rows(out)
    assert photos == ["PXL_1.jpg"]  # (only the one it has)
    assert list(came.values()) == ["Camera/PXL_1.jpg"]
    assert "import_seen" in tables and "portable" in tables
    assert not any("\\" in p for p in came)  # (paths with / between folders, on Windows too)

    # Asked again with nothing changed: no file, just "the same".
    data2, _ = ask_library(phone, [len(sent)], ["Camera/PXL_1.jpg"], version)
    assert json.loads(data2) == {"same": True, "version": version}

    # A photo changed on the phone (another size now): still found by where it came from.
    data3, version3 = ask_library(phone, [123], ["Camera/PXL_1.jpg"], version)
    assert version3 != version
    out.write_bytes(data3)
    assert len(library_rows(out)[0]) == 1

    # Nothing on the phone: nothing in it.
    data4, _ = ask_library(phone, [], [], version)
    out.write_bytes(data4)
    assert library_rows(out)[0] == []


def test_unpairing_drops_the_phones_library_file(setup):
    server, rule, _, _, _ = setup
    phone = paired(server, rule)
    ask_library(phone, [1], [])
    pid = phones.load_phones()[0].id
    assert phones._library_files(pid)[0].is_file()
    phones.forget(pid)
    assert not phones._library_files(pid)[0].exists()
