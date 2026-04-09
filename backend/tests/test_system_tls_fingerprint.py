import asyncio
import builtins
import hashlib
import io
import logging
import os
import socket
import ssl

from backend.api.v1.endpoints import system


class _DummySocket:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _DummyWrappedSocket(_DummySocket):
    def __init__(self, cert_der: bytes):
        self._cert_der = cert_der

    def getpeercert(self, binary_form: bool = False):
        assert binary_form is True
        return self._cert_der


class _DummySSLContext:
    def __init__(self, cert_der: bytes):
        self._cert_der = cert_der
        self.check_hostname = True
        self.verify_mode = ssl.CERT_REQUIRED
        self.minimum_version = None

    def wrap_socket(self, sock, server_hostname: str | None = None):
        return _DummyWrappedSocket(self._cert_der)


def _format_fingerprint(certificate_bytes: bytes) -> str:
    fingerprint = hashlib.sha256(certificate_bytes).hexdigest().upper()
    return ":".join(
        fingerprint[index:index + 2] for index in range(0, len(fingerprint), 2)
    )


def test_get_tls_fingerprint_dynamic_fetch_avoids_sensitive_logging(monkeypatch, caplog):
    cert_der = b"trusted-origin-certificate"
    expected_fingerprint = _format_fingerprint(cert_der)

    monkeypatch.setattr(system, "get_trusted_web_origin", lambda: "https://nojoin.example.com")
    monkeypatch.setattr(ssl, "create_default_context", lambda: _DummySSLContext(cert_der))
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: _DummySocket())

    with caplog.at_level(logging.INFO, logger=system.logger.name):
        result = asyncio.run(system.get_tls_fingerprint(current_user=object()))

    assert result == {"fingerprint": expected_fingerprint}
    assert "Retrieved TLS fingerprint from trusted HTTPS origin." in caplog.text
    assert expected_fingerprint not in caplog.text
    assert "nojoin.example.com" not in caplog.text


def test_get_tls_fingerprint_local_fallback_avoids_sensitive_logging(monkeypatch, caplog):
    cert_der = b"local-certificate"
    expected_fingerprint = _format_fingerprint(cert_der)
    dynamic_error = "sensitive-dynamic-error"

    monkeypatch.setattr(system, "get_trusted_web_origin", lambda: "https://nojoin.example.com")
    monkeypatch.setattr(ssl, "create_default_context", lambda: _DummySSLContext(b"unused"))

    def raise_connection(*args, **kwargs):
        raise TimeoutError(dynamic_error)

    monkeypatch.setattr(socket, "create_connection", raise_connection)
    monkeypatch.setattr(os.path, "exists", lambda path: path == "/etc/nginx/certs/cert.crt")
    monkeypatch.setattr(ssl, "PEM_cert_to_DER_cert", lambda pem_data: cert_der)
    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: io.StringIO("pem-data"))

    with caplog.at_level(logging.INFO, logger=system.logger.name):
        result = asyncio.run(system.get_tls_fingerprint(current_user=object()))

    assert result == {"fingerprint": expected_fingerprint}
    assert "Dynamic TLS certificate retrieval failed; falling back to local certificate." in caplog.text
    assert "Retrieved TLS fingerprint from local certificate fallback." in caplog.text
    assert dynamic_error not in caplog.text
    assert expected_fingerprint not in caplog.text
    assert "nojoin.example.com" not in caplog.text


def test_get_tls_fingerprint_local_failure_avoids_error_leak(monkeypatch, caplog):
    dynamic_error = "sensitive-dynamic-error"
    local_error = "sensitive-local-error"

    monkeypatch.setattr(system, "get_trusted_web_origin", lambda: "https://nojoin.example.com")
    monkeypatch.setattr(ssl, "create_default_context", lambda: _DummySSLContext(b"unused"))

    def raise_connection(*args, **kwargs):
        raise TimeoutError(dynamic_error)

    def raise_open(*args, **kwargs):
        raise OSError(local_error)

    monkeypatch.setattr(socket, "create_connection", raise_connection)
    monkeypatch.setattr(os.path, "exists", lambda path: path == "/etc/nginx/certs/cert.crt")
    monkeypatch.setattr(builtins, "open", raise_open)

    with caplog.at_level(logging.INFO, logger=system.logger.name):
        result = asyncio.run(system.get_tls_fingerprint(current_user=object()))

    assert result == {"fingerprint": None}
    assert "Dynamic TLS certificate retrieval failed; falling back to local certificate." in caplog.text
    assert "Unable to resolve TLS fingerprint from local certificate fallback." in caplog.text
    assert dynamic_error not in caplog.text
    assert local_error not in caplog.text
    assert "nojoin.example.com" not in caplog.text