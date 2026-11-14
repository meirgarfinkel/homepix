"""Downloads trust the system's certificates wherever it keeps them, not only where the bundled OpenSSL
was built to look (Red Hat's /etc/pki/tls, so on Ubuntu every model download failed)."""

import datetime as dt
import ssl
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from homepix.core import util


def _a_ca(path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _openssl_that_trusts_no_one(monkeypatch):
    monkeypatch.setattr(ssl, "create_default_context", lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))


def test_on_ubuntu_the_certificates_are_found_where_debian_keeps_them(tmp_path, monkeypatch):
    debian = tmp_path / "ca-certificates.crt"
    _a_ca(debian)
    monkeypatch.setattr(sys, "platform", "linux")
    _openssl_that_trusts_no_one(monkeypatch)
    monkeypatch.setattr(util, "CA_FILES", (str(tmp_path / "pki" / "ca-bundle.crt"), str(debian)))
    ctx = util.https_context()
    assert [c["subject"] for c in ctx.get_ca_certs()] == [((("commonName", "Test CA"),),)]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname  # (still checked, never turned off)


def test_a_broken_bundle_is_passed_over_for_the_next(tmp_path, monkeypatch):
    broken, good = tmp_path / "broken.pem", tmp_path / "good.pem"
    broken.write_text("not a certificate")
    _a_ca(good)
    monkeypatch.setattr(sys, "platform", "linux")
    _openssl_that_trusts_no_one(monkeypatch)
    monkeypatch.setattr(util, "CA_FILES", (str(broken), str(good)))
    assert len(util.https_context().get_ca_certs()) == 1


def test_certificates_openssl_already_has_are_left_as_they_are(tmp_path, monkeypatch):
    mine = tmp_path / "mine.pem"
    _a_ca(mine)
    other = tmp_path / "other.pem"
    _a_ca(other)

    def with_one():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cafile=str(mine))
        return ctx

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ssl, "create_default_context", with_one)
    monkeypatch.setattr(util, "CA_FILES", (str(other),))
    assert len(util.https_context().get_ca_certs()) == 1
