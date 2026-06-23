import os
import socket
from unittest.mock import patch

from fastapi import Request

from backend.utils.rate_limit import get_client_address


def make_mock_request(client_ip: str | None, headers_dict: dict[str, str]) -> Request:
    headers_list = []
    for k, v in headers_dict.items():
        headers_list.append((k.lower().encode("latin1"), v.encode("latin1")))

    scope = {
        "type": "http",
        "headers": headers_list,
    }
    if client_ip is not None:
        scope["client"] = (client_ip, 12345)
    else:
        scope["client"] = None

    return Request(scope)


def test_direct_access_untrusted_peer():
    # Direct peer is untrusted, headers must be ignored
    req = make_mock_request(
        client_ip="203.0.113.5",
        headers_dict={
            "X-Forwarded-For": "8.8.8.8",
            "X-Real-IP": "8.8.8.8",
        },
    )
    with patch.dict(os.environ, {"NOJOIN_TRUSTED_PROXIES": "127.0.0.1,::1"}):
        assert get_client_address(req) == "203.0.113.5"


def test_single_trusted_proxy():
    # Direct peer is trusted loopback, X-Forwarded-For contains one client IP
    req = make_mock_request(
        client_ip="127.0.0.1",
        headers_dict={
            "X-Forwarded-For": "203.0.113.5",
        },
    )
    with patch.dict(os.environ, {"NOJOIN_TRUSTED_PROXIES": "127.0.0.1,::1"}):
        assert get_client_address(req) == "203.0.113.5"


def test_nested_trusted_proxies():
    # Direct peer is trusted, and there is a trusted proxy subnet.
    # The client IP is the first untrusted IP from the right.
    req = make_mock_request(
        client_ip="127.0.0.1",
        headers_dict={
            "X-Forwarded-For": "203.0.113.5, 192.168.1.10",
        },
    )
    # Trust 127.0.0.1 and the 192.168.1.0/24 network
    with patch.dict(
        os.environ, {"NOJOIN_TRUSTED_PROXIES": "127.0.0.1,::1,192.168.1.0/24"}
    ):
        assert get_client_address(req) == "203.0.113.5"


def test_spoofed_headers_via_trusted_proxy():
    # Direct peer is trusted, but the header contains an untrusted proxy trying to spoof.
    req = make_mock_request(
        client_ip="127.0.0.1",
        headers_dict={
            "X-Forwarded-For": "8.8.8.8, 203.0.113.5",
        },
    )
    # Trust only 127.0.0.1
    with patch.dict(os.environ, {"NOJOIN_TRUSTED_PROXIES": "127.0.0.1"}):
        assert get_client_address(req) == "203.0.113.5"


def test_x_real_ip_fallback():
    # Direct peer is trusted, X-Forwarded-For is absent, X-Real-IP is trusted.
    req = make_mock_request(
        client_ip="127.0.0.1",
        headers_dict={
            "X-Real-IP": "203.0.113.5",
        },
    )
    with patch.dict(os.environ, {"NOJOIN_TRUSTED_PROXIES": "127.0.0.1"}):
        assert get_client_address(req) == "203.0.113.5"


def test_hostname_resolution_in_trusted_list():
    # Direct peer is 172.18.0.2. The hostname 'nginx' resolves to 172.18.0.2.
    req = make_mock_request(
        client_ip="172.18.0.2",
        headers_dict={
            "X-Forwarded-For": "203.0.113.5",
        },
    )

    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host == "nginx":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.2", 80))]
        raise socket.gaierror(-2, "Name or service not known")

    with patch("socket.getaddrinfo", side_effect=mock_getaddrinfo):
        with patch.dict(os.environ, {"NOJOIN_TRUSTED_PROXIES": "127.0.0.1,nginx"}):
            assert get_client_address(req) == "203.0.113.5"


def test_missing_client_info():
    req = make_mock_request(client_ip=None, headers_dict={})
    assert get_client_address(req) == "unknown"


def test_mask_hostname():
    from backend.utils.rate_limit import _mask_hostname

    assert _mask_hostname("") == ""
    assert _mask_hostname("a") == "***"
    assert _mask_hostname("ab") == "***"
    assert _mask_hostname("nginx") == "n***x"
    assert _mask_hostname("myproxy.internal") == "m***l"


def test_hostname_resolution_failure_logging(caplog):
    import logging

    from backend.utils.rate_limit import _resolve_hostname

    def mock_getaddrinfo(host, port, *args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    with patch("socket.getaddrinfo", side_effect=mock_getaddrinfo):
        with caplog.at_level(logging.WARNING):
            _resolve_hostname("nginx-failed-proxy")

    assert len(caplog.records) == 1
    assert "Failed to resolve trusted proxy hostname n***y" in caplog.text
    assert "[Errno -2] Name or service not known" in caplog.text
