"""The part of HomePix that phones talk to, over the home network (see phones.py for what it does).

Encrypted with this computer's own key (TLS 1.2 or newer); the phone trusts only that key, from the QR
code. Every request but pairing carries the phone's secret. Addresses outside the home network are never
answered. It's announced on the network (as SERVICE) so a phone finds the computer when its address
changes.

Requests (JSON in and out, but the file itself):
- POST /v1/pair {code, name}: pair, with the QR code's code. Answers {phone, token, computer}.
- POST /v1/hello: check the secret works. Answers {computer, phone, ready}.
- POST /v1/unpair: the phone was unpaired on the phone: unpair it here too (as Settings does). Answers {}.
- POST /v1/need {files: [{path, size, sha256, qhash}]}: which to send. Answers {need: [{path, have}],
  quick: true}. Asked first with {path, size, qhash} alone, it answers {need: [{path}]}: those the
  library doesn't have by their quick fingerprint (the phone then makes the SHA-256 of only those).
- POST /v1/library {qhashes, sources, version}: what HomePix knows about the photos the phone has (see
  phones.library_for). Answers {same: true} when the phone's version is current, else the file itself
  (a SQLite database like a folder's portable copy), with its version in X-Version.
- PUT /v1/file, the file's bytes from X-Offset on, with X-Path (URL-encoded), X-Size, X-Mtime (ms) and
  X-Sha256: answers {have} (all of it once it's in place), or 409 {have} to go on from there.
"""

import ipaddress
import json
import logging
import os
import shutil
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from . import phones
from .autoimport import load_rules
from .db import open_db

log = logging.getLogger(__name__)

MAX_JSON = 4 << 20
MAX_DRAIN = 64 << 20  # a refused upload's bytes are read and dropped up to this much (else the connection is cut)
MAX_DRAIN_UNPAIRED = 256 << 10  # (from a device that isn't paired, only this much)
MAX_CONNECTIONS = 48  # at once, from everyone
MAX_PER_ADDRESS = 8  # at once, from any one device


def _rule(phone: phones.Phone, paused_too: bool = False):
    """The automation a phone's photos go to, while it's there and on (or paused_too: there at all)."""
    for r in load_rules():
        if r.id == phone.rule_id:
            return r if r.enabled or paused_too else None
    return None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60
    server_version = "HomePix"
    sys_version = ""

    def log_message(self, fmt, *args):
        log.debug("phone %s: " + fmt, self.client_address[0], *args)

    def _send(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _length(self) -> int:
        try:
            n = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise phones.Refused("The length is missing.", 411) from None
        if n < 0:
            raise phones.Refused("The length is missing.", 411)
        return n

    def _json(self) -> dict:
        n = self._length()
        if n > MAX_JSON:
            raise phones.Refused("That's too much at once.", 413)
        try:
            body = json.loads(self._read(n) or b"{}")
        except ValueError:
            raise phones.Refused("That isn't readable.", 400) from None
        if not isinstance(body, dict):
            raise phones.Refused("That isn't readable.", 400)
        return body

    def _phone(self) -> phones.Phone:
        auth = self.headers.get("Authorization", "")
        phone = phones.phone_for(auth[7:] if auth.startswith("Bearer ") else "")
        if phone is None:
            raise phones.Refused("This phone isn't paired with this computer. Pair it again.", 401)
        self._paired = True
        return phone

    def _watch(self, phone: phones.Phone) -> str:
        rule = _rule(phone)
        if rule is None:
            raise phones.Refused("Importing from this phone is paused or removed in HomePix's Settings.", 409)
        return rule.watch

    def _read(self, n: int) -> bytes:
        data = self.rfile.read(n)
        self._left -= len(data)
        return data

    def _handle(self, method: str):
        self._paired = False
        try:
            self._left = max(0, int(self.headers.get("Content-Length", "0")))  # (body bytes not read yet)
        except ValueError:
            self._left = 0
        route = getattr(self, f"_{method}_{self.path.split('?')[0].strip('/').replace('/', '_')}", None)
        try:
            if route is None:
                raise phones.Refused("Not here.", 404)
            route()
        except phones.Elsewhere as e:
            self._drain()
            self._send(409, {"have": e.have, "error": str(e)})
        except phones.Refused as e:
            self._drain()
            self._send(e.status, {"error": str(e)})
        except (ConnectionError, TimeoutError, ssl.SSLError):
            self.close_connection = True
        except Exception:
            log.exception("phone request failed")
            self.close_connection = True
            self._send(500, {"error": "Something went wrong on the computer."})

    def _drain(self):
        """Read what's left of a refused request's body, so the phone gets the answer (cutting the
        connection with bytes unread makes some systems drop the answer too)."""
        if self._left > (MAX_DRAIN if self._paired else MAX_DRAIN_UNPAIRED):
            self.close_connection = True
            return
        while self._left > 0:
            if not self._read(min(self._left, 1 << 20)):
                self.close_connection = True
                return

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_GET(self):
        self._handle("GET")

    # --- requests

    def _POST_v1_pair(self):
        body = self._json()
        phone, token = phones.pair(str(body.get("code", "")), str(body.get("name", "")), self.client_address[0])
        self.server.events("paired", phone)
        self._send(200, {"phone": phone.id, "token": token, "computer": phones.computer_name()})

    def _POST_v1_hello(self):
        phone = self._phone()
        self._json()
        phones.touch(phone.id)
        self._send(200, {"computer": phones.computer_name(), "phone": phone.name, "ready": _rule(phone) is not None})

    def _POST_v1_unpair(self):
        phone = self._phone()
        self._json()
        phones.unpair(phone.id)
        self._send(200, {})
        self.server.events("unpaired", phone)

    def _POST_v1_need(self):
        phone = self._phone()
        body = self._json()
        phones.touch(phone.id)  # (in touch: Settings shows when)
        watch = self._watch(phone)
        files = body.get("files")
        if not isinstance(files, list):
            raise phones.Refused("That isn't readable.", 400)
        db = open_db()
        try:
            need = phones.needs(db, watch, [f for f in files if isinstance(f, dict)])
        finally:
            db.close()
        self._send(200, {"need": need, "quick": True})

    def _POST_v1_library(self):
        phone = self._phone()
        body = self._json()
        # (a phone before 0.7 tells only its files' sizes: it gets only the photos it sent)
        qhashes, sources = body.get("qhashes", []), body.get("sources", [])
        if not isinstance(qhashes, list) or not isinstance(sources, list):
            raise phones.Refused("That isn't readable.", 400)
        rule = _rule(phone, paused_too=True)
        version, path = phones.library_for(phone, qhashes, sources, rule.watch if rule else None)
        if body.get("version") == version:
            self._send(200, {"same": True, "version": version})
            return
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.sqlite3")
            self.send_header("Content-Length", str(size))
            self.send_header("X-Version", version)
            self.end_headers()
            shutil.copyfileobj(f, self.wfile, 1 << 20)

    def _PUT_v1_file(self):
        phone = self._phone()
        watch = self._watch(phone)
        h = self.headers
        try:
            rel = unquote(h.get("X-Path", ""), errors="strict")
            size, mtime, offset = int(h["X-Size"]), int(h["X-Mtime"]), int(h.get("X-Offset", "0"))
            sha = h["X-Sha256"]
        except (KeyError, ValueError, UnicodeDecodeError):
            raise phones.Refused("Something about the file is missing.", 400) from None
        have = phones.receive(watch, rel, size, mtime, sha, offset, self._read, self._length())
        if have == size:
            phones.touch(phone.id, sent=True)
            self.server.events("arrived", phone)
        self._send(200, {"have": have})


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"  # (on Windows it would let another program take the port too)
    request_queue_size = 16

    def __init__(self, addr, ctx: ssl.SSLContext, events):
        super().__init__(addr, _Handler, bind_and_activate=True)
        # (the handshake happens in each request's own thread, so a slow phone holds up no one)
        self.socket = ctx.wrap_socket(self.socket, server_side=True, do_handshake_on_connect=False)
        self.events = events
        self._open: dict[str, int] = {}  # connections open, by device
        self._count_lock = threading.Lock()

    def verify_request(self, request, client_address) -> bool:
        """Before the handshake: only a device on a private network, reaching this computer at an address
        it announces (not a VPN's, a container's or a virtual machine's), and not too many at once."""
        peer = client_address[0]
        try:
            here = request.getsockname()[0]
        except OSError:
            return False
        if not phones.private(peer) or not _answers_at(here):
            return False
        with self._count_lock:
            if sum(self._open.values()) >= MAX_CONNECTIONS or self._open.get(peer, 0) >= MAX_PER_ADDRESS:
                return False
            self._open[peer] = self._open.get(peer, 0) + 1
        return True

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._count_lock:
                left = self._open.get(client_address[0], 1) - 1
                if left > 0:
                    self._open[client_address[0]] = left
                else:
                    self._open.pop(client_address[0], None)


_here: tuple[float, set[str]] = (0.0, set())


def _answers_at(ip: str) -> bool:
    """Is this one of the addresses phones are told about (or this computer's own loopback, which an
    emulator's connections come in on)? Read again at most every 30 seconds."""
    global _here
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if a.is_loopback or (isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped and a.ipv4_mapped.is_loopback):
        return True
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        ip = str(a.ipv4_mapped)
    when, known = _here
    if time.time() - when > 30 or ip not in known:
        _here = (time.time(), set(phones.addresses()))
    return ip in _here[1]


def _label(text: str) -> str:
    """text cut to fit one DNS name part (63 bytes, whole characters). A longer computer name made
    announcing fail, so phones never found the computer."""
    return text.encode()[:63].decode(errors="ignore").strip()


def _context() -> ssl.SSLContext:
    me = phones.identity()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(me.cert, me.key)
    return ctx


class Server:
    """Answering phones, on PORT if it's free (else another, kept for next time), and announced on the
    network. events(kind, phone) is called (from its own thread) when a phone pairs ("paired"), unpairs
    itself ("unpaired") or a file has arrived ("arrived")."""

    def __init__(self, events=lambda kind, phone: None, port: int | None = None, announce: bool = True):
        self.events = events
        self.want_port = port
        self.announce = announce
        self._announcing = None
        self.port = None
        self._http = None
        self._zc = None
        self._info = None
        self._announced: list[str] = []
        self._stopping = threading.Event()

    def start(self):
        from .settings import changing_settings, load_settings

        port = self.want_port if self.want_port is not None else load_settings().get("phone_port", phones.PORT)
        ctx = _context()
        try:
            self._http = _Server(("0.0.0.0", port), ctx, self.events)
        except OSError:
            self._http = _Server(("0.0.0.0", 0), ctx, self.events)
        self.port = self._http.server_address[1]
        if self.want_port is None and self.port != port:
            with changing_settings() as s:
                s["phone_port"] = self.port
        threading.Thread(target=self._http.serve_forever, name="phones", daemon=True).start()
        if self.announce:
            self._stopping.clear()
            self._announcing = threading.Thread(target=self._keep_announced, name="phones-announce", daemon=True)
            self._announcing.start()

    def _keep_announced(self, every: float = 60):
        """Announce, and again whenever this computer's addresses change (on a network only after
        login, say, or moved to another one)."""
        while True:
            self.announce_if_changed()
            if self._stopping.wait(every):
                return

    def announce_if_changed(self):
        ips = phones.addresses()
        if ips == self._announced:
            return
        self._unannounce()
        self._announced = ips
        if ips:
            self._announce(ips)

    def _unannounce(self):
        if self._zc is not None:
            try:
                if self._info is not None:
                    self._zc.unregister_service(self._info)
                self._zc.close()
            except Exception:
                pass
            self._zc = None
            self._info = None

    def _announce(self, ips):
        try:
            from zeroconf import ServiceInfo, Zeroconf

            name = _label(f"HomePix on {phones.computer_name()}")
            self._info = ServiceInfo(
                phones.SERVICE,
                f"{name}.{phones.SERVICE}",
                addresses=[socket.inet_aton(ip) for ip in ips],
                port=self.port,
                properties={"f": phones.identity().fingerprint[:16]},
                server=f"{_label(phones.computer_name())}.local.",
            )
            self._zc = Zeroconf(interfaces=ips)
            self._zc.register_service(self._info, allow_name_change=True)
        except Exception:
            log.warning("announcing HomePix on the network didn't work", exc_info=True)

    def stop(self):
        self._stopping.set()
        if self._announcing is not None:
            self._announcing.join(10)
            self._announcing = None
        self._unannounce()
        self._announced = []
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
            self._http = None
