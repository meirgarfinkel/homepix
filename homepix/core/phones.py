"""Phones paired with this computer, sending their photos to HomePix over the home network.

Pairing: the computer shows a QR code with its addresses, the fingerprint of its key and a one-time
code. The phone app scans it, connects (encrypted, and trusting only a computer with that key), and
trades the code for a secret of its own, which it sends with everything after that. The code works
once, for PAIR_MINUTES, while the pairing window is open.

Photos: the phone asks which of its files the computer still needs (by content, so nothing already in
the library is sent again), then sends each one into its automation's Import from folder, at the same
place it has on the phone (Camera/..., WhatsApp/...). A file is written to a hidden folder first, its
content checked, and only then put in place, so the import never sees half a file. An upload that's cut
off goes on from where it stopped. Deleting is never sent, either way.

Only addresses on the home network are answered (see private()).
"""

import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import autoimport
from .autoimport import MEDIA_EXTS
from .config import data_dir
from .util import load_json, quick_hash, save_json, within

PORT = 47820  # (another is taken when it's in use, and kept)
SERVICE = "_homepix._tcp.local."
PAIR_MINUTES = 10
PAIR_TRIES = 20  # wrong codes before a pairing is closed
INCOMING = ".homepix-incoming"  # in the Import from folder: files still arriving (hidden: never imported)
SPARE = 1 << 30  # an upload is refused when it would leave less than this free on the disk
MAX_NEED = 2000  # files asked about at once

_lock = threading.RLock()


def folder() -> Path:
    f = data_dir() / "phones"
    f.mkdir(exist_ok=True)
    return f


# --- this computer's key -------------------------------------------------------------------------------


@dataclass
class Identity:
    cert: Path
    key: Path
    fingerprint: str  # SHA-256 of the certificate, in hex: what the phone checks


def _fingerprint(pem: bytes) -> str:
    import ssl

    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem.decode())).hexdigest()


def identity() -> Identity:
    """This computer's key and certificate, made the first time (and kept: phones paired with it trust
    only this one)."""
    cert, key = folder() / "computer.crt", folder() / "computer.key"
    with _lock:
        if not (cert.is_file() and key.is_file()):
            _make_identity(cert, key)
    return Identity(cert, key, _fingerprint(cert.read_bytes()))


def _make_identity(cert_path: Path, key_path: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HomePix")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=36500))  # (the phone checks the fingerprint, not dates)
        .sign(key, hashes.SHA256())
    )
    tmp = key_path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
    os.replace(tmp, key_path)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


# --- the paired phones, in phones/phones.json ------------------------------------------------------------


@dataclass
class Phone:
    name: str
    rule_id: str  # the import automation its photos go to
    token_sha: str = ""  # SHA-256 of its secret (the secret itself is only on the phone)
    paired: str = ""
    seen: str = ""  # when it last got in touch
    sent: str = ""  # when a file from it last arrived
    own_rule: bool = False  # its automation was made for it when it paired (and goes when it's unpaired)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @classmethod
    def from_dict(cls, d: dict) -> "Phone":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def _phones_file() -> Path:
    return folder() / "phones.json"


def load_phones() -> list[Phone]:
    with _lock:
        rows = load_json(_phones_file(), [])  # (a damaged file is never read as "no phones": load_json)
    out = []
    for d in rows if isinstance(rows, list) else []:
        try:
            out.append(Phone.from_dict(d))
        except TypeError:
            continue
    return out


def save_phones(phones: list[Phone]):
    with _lock:
        save_json(_phones_file(), [asdict(p) for p in phones])


def touch(phone_id: str, sent: bool = False):
    """A phone got in touch (and sent a file): noted for Settings to show."""
    with _lock:
        ps = load_phones()
        for p in ps:
            if p.id == phone_id:
                p.seen = _now()
                if sent:
                    p.sent = p.seen
        save_phones(ps)


def forget(phone_id: str):
    """Unpair a phone: its secret stops working at once."""
    with _lock:
        save_phones([p for p in load_phones() if p.id != phone_id])
    for f in _library_files(phone_id):
        f.unlink(missing_ok=True)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def phone_for(token: str) -> Phone | None:
    """The paired phone with this secret, or None."""
    if not token:
        return None
    want = _sha(token)
    found = None
    for p in load_phones():
        if hmac.compare_digest(p.token_sha, want):  # (every one is compared: no timing hints)
            found = p
    return found


# --- pairing ---------------------------------------------------------------------------------------------


class Refused(Exception):
    """The phone's request can't be done (the message says why, for the phone to show)."""

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


@dataclass
class Pairing:
    rule_id: str
    new_rule: bool = False  # the automation is made for the phone once it has paired
    code: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    expires: float = field(default_factory=lambda: time.time() + PAIR_MINUTES * 60)
    tries: int = 0
    phone: Phone | None = None  # once a phone has paired


_pairing: Pairing | None = None


def open_pairing(rule_id: str | None = None) -> Pairing:
    """Start pairing a phone: to an automation, or (None) to one made for it once it has paired (see
    make_rule). One at a time; a new one replaces the last."""
    global _pairing
    with _lock:
        _pairing = Pairing(rule_id or uuid.uuid4().hex[:12], new_rule=rule_id is None)
        return _pairing


def close_pairing():
    global _pairing
    with _lock:
        _pairing = None


def pairing() -> Pairing | None:
    return _pairing


def pair(code: str, name: str) -> tuple[Phone, str]:
    """A phone trading the QR code's code for its own secret: (the phone, its secret)."""
    global _pairing
    with _lock:
        p = _pairing
        if p is None or p.phone is not None or time.time() > p.expires:
            raise Refused("Pairing isn't open on the computer. Open it again in HomePix's Settings.")
        if not hmac.compare_digest(code.encode(), p.code.encode()):
            p.tries += 1
            if p.tries >= PAIR_TRIES:
                _pairing = None
            raise Refused("That code isn't right. Scan the code on the computer again.")
        token = secrets.token_urlsafe(32)
        name = " ".join(str(name).split())[:60] or "Phone"
        phone = Phone(name=name, rule_id=p.rule_id, token_sha=_sha(token), paired=_now(), own_rule=p.new_rule)
        save_phones([*load_phones(), phone])
        p.phone = phone
        return phone, token


def free_rules(rules: list) -> list:
    """The automations no paired phone sends to: a phone can be paired to one of these. (Two phones on one
    would mix up files with the same names, like WhatsApp's.)"""
    taken = {p.rule_id for p in load_phones()}
    return [r for r in rules if r.id not in taken]


def _folder_name(name: str) -> str:
    bare = "".join(c for c in name if c not in '<>:"/\\|?*' and ord(c) >= 32).strip(" .")
    return bare if bare and bare.split(".")[0].lower() not in _BAD_NAMES else "Phone"


def _has_files(folder: Path) -> bool:
    return any(files for _, _, files in os.walk(folder))


def make_rule(name: str, rule_id: str, folders: list[str], rules: list):
    """The import automation made for a phone when it pairs: its photos arrive in a folder of its own,
    "Phone photos/<its name>" in the home folder (made now), and are named by date into year folders in
    the first library folder. Returns (the automation, whether its folder had to be excluded from the
    library: when the library holds the home folder)."""
    from .autoimport import Rule
    from .settings import excluded_folders, set_excluded

    base, n = Path.home() / "Phone photos", 1
    while True:
        watch = base / (_folder_name(name) + (f" {n}" if n > 1 else ""))
        taken = any(within(str(watch), [r.watch]) or within(r.watch, [str(watch)]) for r in rules)
        if not taken and not _has_files(watch):  # (a phone's old folder, left empty, is used again)
            break
        n += 1
    watch.mkdir(parents=True, exist_ok=True)
    rule = Rule(name=name, watch=str(watch), dest=folders[0], tidy=True, id=rule_id)
    inside = within(str(watch), folders) and not within(str(watch), excluded_folders())
    if inside:
        set_excluded(str(watch), True)
    return rule, inside


def _now() -> str:
    return dt.datetime.now().isoformat(sep=" ", timespec="seconds")


def computer_name() -> str:
    return socket.gethostname().split(".")[0] or "this computer"


_VIRTUAL = ("docker", "veth", "virbr", "br-", "vmnet", "vboxnet", "tun", "tap", "wg", "zt", "lo")


def addresses() -> list[str]:
    """This computer's addresses on the home network (IPv4; not loopback or virtual machines')."""
    import ifaddr

    out = []
    for a in ifaddr.get_adapters():
        nice = (a.nice_name or a.name or "").lower()
        if nice.startswith(_VIRTUAL) or "virtual" in nice or "vethernet" in nice:
            continue
        for ip in a.ips:
            if not isinstance(ip.ip, str):
                continue  # (IPv6)
            addr = ipaddress.ip_address(ip.ip)
            if addr.is_private and not addr.is_loopback and not addr.is_link_local and ip.ip not in out:
                out.append(ip.ip)
    return out


def qr_text(p: Pairing, port: int) -> str:
    """What the QR code holds: where to find this computer, its key's fingerprint (in base64url), and
    the code."""
    import base64
    from urllib.parse import urlencode

    fp = base64.urlsafe_b64encode(bytes.fromhex(identity().fingerprint)).decode().rstrip("=")  # (shorter)
    q = {"v": 1, "a": ",".join(addresses()), "p": port, "f": fp, "c": p.code}
    return "homepix:pair?" + urlencode({**q, "n": computer_name()})


def private(host: str) -> bool:
    """Is this address on a home network (or this computer)? Anything else is never answered."""
    try:
        a = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_private or a.is_loopback or a.is_link_local


# --- receiving photos ------------------------------------------------------------------------------------

_BAD_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}


def safe_path(rel: str) -> str:
    """A phone's path for a file (Camera/IMG_1.jpg), checked: no way out of the folder, no hidden or
    odd names, only photos and videos. Refused otherwise."""
    if not isinstance(rel, str) or not rel or len(rel) > 1024 or "\\" in rel or "\0" in rel:
        raise Refused("That file name can't be used.", 400)
    parts = rel.split("/")
    for part in parts:
        bare = part.rstrip(". ")
        if (
            not part
            or part.startswith(".")
            or bare != part
            or len(part.encode()) > 255
            or any(c in part for c in '<>:"|?*')
            or any(ord(c) < 32 for c in part)
            or bare.split(".")[0].lower() in _BAD_NAMES
        ):
            raise Refused("That file name can't be used.", 400)
    if os.path.splitext(parts[-1])[1].lower() not in MEDIA_EXTS:
        raise Refused("Only photos and videos can be sent.", 400)
    return "/".join(parts)


def _target(watch: str, rel: str) -> Path:
    t = Path(watch, *safe_path(rel).split("/"))
    if not t.resolve().is_relative_to(Path(watch).resolve()):
        raise Refused("That file name can't be used.", 400)
    return t


def _part(watch: str, sha: str) -> Path:
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise Refused("That fingerprint isn't right.", 400)
    return Path(watch, INCOMING, sha + ".part")


def _hash(path: Path) -> str:
    d = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            d.update(chunk)
    return d.hexdigest()


def _have_content(db, sha: str, qhash: str | None, watch: str) -> bool:
    """Is this content in the library, or was it imported before (and since deleted, or its import
    undone)? Then it's never sent again. (qhash: its quick fingerprint, to find it in the library by.)"""
    from . import duplicates

    if db.execute("SELECT 1 FROM import_seen WHERE sha256=? LIMIT 1", (sha,)).fetchone():
        return True
    if db.execute("SELECT 1 FROM photos WHERE sha256=? LIMIT 1", (sha,)).fetchone():
        return True
    if qhash is None:
        return False
    rows = _in_library(db, qhash, watch)
    return bool(rows) and sha in duplicates._sha256(db, rows).values()


def _in_library(db, qhash: str, watch: str) -> list:
    """The library's photos with this quick fingerprint (not counting the Import from folder: when it's
    inside the library, its photos are indexed, but they're on their way in)."""
    rows = db.execute("SELECT id, path, sha256 FROM photos WHERE qhash=?", (qhash,)).fetchall()
    return [r for r in rows if not within(r["path"], [watch])]


def _qhash(value) -> str:
    q = str(value).lower()
    if len(q) != 40 or any(c not in "0123456789abcdef" for c in q):
        raise ValueError(q)
    return q


def needs(db, watch: str, files: list[dict]) -> list[dict]:
    """Which of these files the computer still needs. Each is {path, size, sha256, qhash} (qhash, its
    quick fingerprint (util.quick_hash), optional), or, asked first, {path, size, qhash}: then one the
    library has by its quick fingerprint isn't needed, and the phone makes the SHA-256 only of the
    others. Answers [{path, have}], have being how much of it already came (an upload cut off goes on
    from there; left out when asked by qhash alone)."""
    if len(files) > MAX_NEED:
        raise Refused(f"At most {MAX_NEED} files at a time.", 400)
    _clear_parts(watch)
    out = []
    for f in files:
        try:
            rel, size = f["path"], int(f["size"])
            qhash = _qhash(f["qhash"]) if f.get("qhash") is not None else None
            sha = str(f["sha256"]).lower() if f.get("sha256") is not None else None
            if sha is None and qhash is None:
                continue
            target = _target(watch, rel)
            part = _part(watch, sha) if sha else None
        except (KeyError, TypeError, ValueError, Refused):
            continue  # (one it can't send: left out)
        try:
            if target.is_file() and target.stat().st_size == size and _same(target, size, sha, qhash):
                continue  # here already, waiting to be imported
        except OSError:
            pass
        if sha is None:
            if not _in_library(db, qhash, watch):
                out.append({"path": rel})
            continue
        if _have_content(db, sha, qhash, watch):
            continue
        have = part.stat().st_size if part.is_file() else 0
        out.append({"path": rel, "have": have if have <= size else 0})
    return out


def _same(path: Path, size: int, sha: str | None, qhash: str | None) -> bool:
    return _hash(path) == sha if sha else quick_hash(path, size) == qhash


PART_DAYS = 7  # an upload cut off and not gone on with for this long is dropped


def _clear_parts(watch: str):
    """Drop uploads cut off long ago (the photo deleted on the phone before it was all sent, say)."""
    try:
        parts = list(Path(watch, INCOMING).glob("*.part"))
    except OSError:
        return
    for p in parts:
        lock = _busy(str(p))
        if not lock.acquire(blocking=False):
            continue  # (coming in now)
        try:
            if time.time() - p.stat().st_mtime > PART_DAYS * 86400:
                p.unlink()
        except OSError:
            pass
        finally:
            lock.release()


_receiving: dict[str, threading.Lock] = {}


def _busy(key: str) -> threading.Lock:
    with _lock:
        return _receiving.setdefault(key, threading.Lock())


def receive(watch: str, rel: str, size: int, mtime_ms: int, sha: str, offset: int, read, length: int) -> int:
    """Take part of a file (length bytes from read(n), starting at offset): how much of it has come
    now. When it's all come and its content checks out, it's put at its place in the watch folder (a
    changed version replacing the one there) with the phone's time. Refused when offset isn't where the
    last part stopped (the phone then goes on from what's here)."""
    if not Path(watch).is_dir():
        raise Refused("The folder these photos go to isn't there. Check the import automation in HomePix.", 409)
    sha = sha.lower()
    target, part = _target(watch, rel), _part(watch, sha)
    if size < 0 or offset < 0 or length < 0 or offset + length > size:
        raise Refused("That doesn't add up.", 400)
    lock = _busy(str(part))
    if not lock.acquire(blocking=False):
        raise Refused("That file is already being sent.", 409)
    try:
        part.parent.mkdir(parents=True, exist_ok=True)
        have = part.stat().st_size if part.is_file() else 0
        if have != offset:
            raise Elsewhere(have)
        if shutil.disk_usage(part.parent).free < size - offset + SPARE:
            raise Refused("The computer's disk is too full.", 507)
        with open(part, "ab") as f:
            left = length
            while left:
                chunk = read(min(left, 1 << 20))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        have = part.stat().st_size
        if have < size:
            return have
        if _hash(part) != sha:
            part.unlink()
            raise Refused("The file didn't arrive whole. It will be sent again.", 422)
        os.utime(part, ns=(time.time_ns(), mtime_ms * 1_000_000))
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, target)
        autoimport.arrived_whole(target)
        return size
    finally:
        lock.release()


class Elsewhere(Refused):
    """The upload should go on from have bytes."""

    def __init__(self, have: int):
        super().__init__("Go on from where it stopped.", 409)
        self.have = have


# --- the library's details, back to the phone ----------------------------------------------------------

MAX_FILES = 200_000  # files a phone can tell about at once


def _library_files(phone_id: str) -> tuple[Path, Path]:
    return folder() / f"{phone_id}.db", folder() / f"{phone_id}.version"


def library_for(phone: Phone, qhashes: list, sources: list, watch: str | None) -> tuple[str, Path]:
    """What HomePix knows about the photos this phone has (a phone tells the quick fingerprints of its
    files, util.quick_hash, and their places, as it sends them: "Camera/PXL_1.jpg"), as a small library
    file: (its version, the file). Only photos the phone has go in it. watch: the phone's Import from
    folder (photos are found by where they came from only among those imported from it). Made again only
    when the library, its settings or the phone's files have changed since."""
    from .portable import db_stamp, phone_copy
    from .settings import load_settings

    if len(qhashes) > MAX_FILES or len(sources) > MAX_FILES:
        raise Refused("That's too many files at once.", 413)
    try:
        qhashes = sorted({_qhash(x) for x in qhashes})
    except (TypeError, ValueError):
        raise Refused("That isn't readable.", 400) from None
    sources = sorted({str(x) for x in sources})
    s = load_settings()
    stamp = json.dumps([db_stamp(), s.get("folders"), s.get("excluded"), watch, qhashes, sources])
    version = hashlib.sha256(stamp.encode()).hexdigest()[:32]
    db, ver = _library_files(phone.id)
    with _busy(str(db)):
        if not (db.is_file() and ver.is_file() and ver.read_text() == version):
            phone_copy(db, set(qhashes), set(sources), watch)
            ver.write_text(version)
    return version, db
