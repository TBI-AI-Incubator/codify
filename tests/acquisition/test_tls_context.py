"""The transport's TLS handshake must not carry an ALPN offer."""

from __future__ import annotations

import datetime as dt
import hashlib
import ssl
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from codify.acquisition.politeness import client_tls_context


def _self_signed_pem(tmp_path) -> Path:  # type: ignore[no-untyped-def]
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem = tmp_path / "srv.pem"
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        + cert.public_bytes(serialization.Encoding.PEM)
    )
    return pem


def _server_context(pem: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(pem)
    ctx.set_alpn_protocols(["h2", "http/1.1"])
    return ctx


def _subject_hash_cn(common_name: str) -> str:
    """OpenSSL's hashed-directory name for a subject of one commonName."""
    value = common_name.encode()
    utf8 = bytes([0x0C, len(value)]) + value
    attr = bytes([0x30, 5 + len(utf8), 0x06, 0x03, 0x55, 0x04, 0x03]) + utf8
    canonical = bytes([0x31, len(attr)]) + attr
    digest = hashlib.sha1(canonical, usedforsecurity=False).digest()
    return format(int.from_bytes(digest[:4], "little"), "08x")


def _handshake(server: ssl.SSLContext, client: ssl.SSLContext) -> str | None:
    """Run an in-memory handshake and return the ALPN the server selected."""
    c_in, c_out, s_in, s_out = (ssl.MemoryBIO() for _ in range(4))
    c = client.wrap_bio(c_in, c_out, server_side=False, server_hostname="localhost")
    s = server.wrap_bio(s_in, s_out, server_side=True)
    for _ in range(10):
        for peer, out, peer_in in ((c, c_out, s_in), (s, s_out, c_in)):
            try:
                peer.do_handshake()
            except ssl.SSLWantReadError:
                pass
            peer_in.write(out.read())
        try:
            c.do_handshake()
            s.do_handshake()
            return s.selected_alpn_protocol()
        except ssl.SSLWantReadError:
            continue
    pytest.fail("handshake did not complete")


def test_transport_context_offers_no_alpn(tmp_path) -> None:  # type: ignore[no-untyped-def]
    server = _server_context(_self_signed_pem(tmp_path))
    client = client_tls_context()
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    client.set_alpn_protocols(["http/1.1"])  # what the HTTP layer does at connect
    assert _handshake(server, client) is None


def test_plain_context_control_offers_alpn(tmp_path) -> None:  # type: ignore[no-untyped-def]
    server = _server_context(_self_signed_pem(tmp_path))
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    client.set_alpn_protocols(["http/1.1"])
    assert _handshake(server, client) == "http/1.1"


def test_environment_trust_roots_are_honoured(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pem = _self_signed_pem(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(pem))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    subjects = [c["subject"] for c in client_tls_context().get_ca_certs()]
    assert ((("commonName", "localhost"),),) in subjects


def test_default_trust_roots_are_certifi(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    subjects = [c["subject"] for c in client_tls_context().get_ca_certs()]
    assert ((("commonName", "localhost"),),) not in subjects
    assert subjects


def test_verification_matches_the_runtime_default() -> None:
    default = ssl.create_default_context()
    ctx = client_tls_context()
    assert ctx.verify_flags == default.verify_flags
    assert ctx.options & default.options == default.options
    assert (ctx.check_hostname, ctx.verify_mode) == (True, ssl.CERT_REQUIRED)


def test_cert_dir_alone_is_loaded(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pem = _self_signed_pem(tmp_path)
    cert_only = pem.read_text().split("-----BEGIN CERTIFICATE-----")[1]
    capath = tmp_path / "certs"
    capath.mkdir()
    (capath / f"{_subject_hash_cn('localhost')}.0").write_text(
        "-----BEGIN CERTIFICATE-----" + cert_only
    )
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("SSL_CERT_DIR", str(capath))
    client = client_tls_context()
    assert _handshake(_server_context(pem), client) is None  # verified via the directory
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "empty"))
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(_server_context(pem), client_tls_context())


def test_keylog_file_is_carried(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "keys.log"))
    assert client_tls_context().keylog_filename == str(tmp_path / "keys.log")


def test_cert_file_wins_over_cert_dir(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pem = _self_signed_pem(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(pem))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    subjects = [c["subject"] for c in client_tls_context().get_ca_certs()]
    assert subjects == [((("commonName", "localhost"),),)]
