"""The transport's TLS handshake must not carry an ALPN offer."""

from __future__ import annotations

import datetime as dt
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


def _server_context(tmp_path) -> ssl.SSLContext:  # type: ignore[no-untyped-def]
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(_self_signed_pem(tmp_path))
    ctx.set_alpn_protocols(["h2", "http/1.1"])
    return ctx


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
    server = _server_context(tmp_path)
    client = client_tls_context()
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    client.set_alpn_protocols(["http/1.1"])  # what the HTTP layer does at connect
    assert _handshake(server, client) is None


def test_plain_context_control_offers_alpn(tmp_path) -> None:  # type: ignore[no-untyped-def]
    server = _server_context(tmp_path)
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
