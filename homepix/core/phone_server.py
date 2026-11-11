"""The part of HomePix that phones talk to, over the home network (see phones.py for what it does).

Encrypted with this computer's own key (TLS 1.2 or newer); the phone trusts only that key, from the QR
code. Every request but pairing carries the phone's secret. Addresses outside the home network are never
answered. It's announced on the network (as SERVICE) so a phone finds the computer when its address
changes.

Requests (JSON in and out, but the file itself):
- POST /v1/pair {code, name}: pair, with the QR code's code. Answers {phone, token, computer}.
- POST /v1/hello: check the secret works. Answers {computer, phone, ready}.
- POST /v1/need {files: [{path, size, sha256}]}: which to send. Answers {need: [{path, have}]}.
- PUT /v1/file, the file's bytes from X-Offset on, with X-Path (URL-encoded), X-Size, X-Mtime (ms) and
  X-Sha256: answers {have} (all of it once it's in place), or 409 {have} to go on from there.
"""

import json
import logging
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from . import phones
from .autoimport import load_rules
from .db import open_db

log = logging.getLogger(__name__)

MAX_JSON = 4 << 20


def _rule(phone: phones.Phone):
    """The automation a phone's photos go to, while it's there and on."""
    for r in load_rules():
        if r.id == phone.rule_id:
            return r if r.enabled else None
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
            body = json.loads(self.rfile.read(n) or b"{}")
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
        return phone

    def _watch(self, phone: phones.Phone) -> str:
        rule = _rule(phone)
        if rule is None:
            raise phones.Refused("Importing from this phone is paused or removed in HomePix's Settings.", 409)
        return rule.watch

    def _handle(self, method: str):
        if not phones.private(self.client_address[0]):
            self.close_connection = True
            return
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
        """A refused request's body isn't read: the connection can't be used again."""
        self.close_connection = True

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_GET(self):
        self._handle("GET")

    # --- requests

    def _POST_v1_pair(self):
        body = self._json()
        phone, token = phones.pair(str(body.get("code", "")), str(body.get("name", "")))
        self.server.events("paired", phone)
        self._send(200, {"phone": phone.id, "token": token, "computer": phones.computer_name()})

    def _POST_v1_hello(self):
        self._json()
        phone = self._phone()
        phones.touch(phone.id)
        self._send(200, {"computer": phones.computer_name(), "phone": phone.name, "ready": _rule(phone) is not None})

    def _POST_v1_need(self):
        body = self._json()
        watch = self._watch(self._phone())
        files = body.get("files")
        if not isinstance(files, list):
            raise phones.Refused("That isn't readable.", 400)
        db = open_db()
        try:
            need = phones.needs(db, watch, [f for f in files if isinstance(f, dict)])
        finally:
            db.close()
        self._send(200, {"need": need})

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
        have = phones.receive(watch, rel, size, mtime, sha, offset, self.rfile.read, self._length())
        if have == size:
            phones.touch(phone.id, sent=True)
            self.server.events("arrived", phone)
        self._send(200, {"have": have})


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(self, addr, ctx: ssl.SSLContext, events):
        super().__init__(addr, _Handler, bind_and_activate=True)
        # (the handshake happens in each request's own thread, so a slow phone holds up no one)
        self.socket = ctx.wrap_socket(self.socket, server_side=True, do_handshake_on_connect=False)
        self.events = events


def _context() -> ssl.SSLContext:
    me = phones.identity()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(me.cert, me.key)
    return ctx


class Server:
    """Answering phones, on PORT if it's free (else another, kept for next time), and announced on the
    network. events(kind, phone) is called (from its own thread) when a phone pairs ("paired") or a file
    has arrived ("arrived")."""

    def __init__(self, events=lambda kind, phone: None, port: int | None = None, announce: bool = True):
        self.events = events
        self.want_port = port
        self.announce = announce
        self._announcing = None
        self.port = None
        self._http = None
        self._zc = None
        self._info = None

    def start(self):
        from .settings import load_settings, save_settings

        port = self.want_port if self.want_port is not None else load_settings().get("phone_port", phones.PORT)
        ctx = _context()
        try:
            self._http = _Server(("0.0.0.0", port), ctx, self.events)
        except OSError:
            self._http = _Server(("0.0.0.0", 0), ctx, self.events)
        self.port = self._http.server_address[1]
        if self.want_port is None and self.port != port:
            s = load_settings()
            s["phone_port"] = self.port
            save_settings(s)
        threading.Thread(target=self._http.serve_forever, name="phones", daemon=True).start()
        if self.announce:
            self._announcing = threading.Thread(target=self._announce, name="phones-announce", daemon=True)
            self._announcing.start()

    def _announce(self):
        try:
            from zeroconf import ServiceInfo, Zeroconf

            ips = phones.addresses()
            if not ips:
                return
            name = f"HomePix on {phones.computer_name()}"
            self._info = ServiceInfo(
                phones.SERVICE,
                f"{name}.{phones.SERVICE}",
                addresses=[socket.inet_aton(ip) for ip in ips],
                port=self.port,
                properties={"f": phones.identity().fingerprint[:16]},
                server=f"{phones.computer_name()}.local.",
            )
            self._zc = Zeroconf(interfaces=ips)
            self._zc.register_service(self._info, allow_name_change=True)
        except Exception:
            log.warning("announcing HomePix on the network didn't work", exc_info=True)

    def stop(self):
        if self._announcing is not None:
            self._announcing.join(10)
            self._announcing = None
        if self._zc is not None:
            try:
                if self._info is not None:
                    self._zc.unregister_service(self._info)
                self._zc.close()
            except Exception:
                pass
            self._zc = None
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
            self._http = None
