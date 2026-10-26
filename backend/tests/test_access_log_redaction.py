"""Secrets in request URLs are masked in the access logs.

uvicorn's access log prints each request line with its query string, and the
setup account can download that log from Settings. The calendar sign-in
callback carries a one-time code, its state and Microsoft's session_state
there, and an invitation link carries its code. These tests send a request
through a real uvicorn server and check that only those values are masked: the
path, every other parameter and the rest of the line stay as they were.

The bundled nginx masks the same names with a chain of regex maps in
nginx/access-log.conf. Nothing in the test environment runs nginx, so the
tests below replay those maps with Python's re, which reads these patterns the
same way PCRE does.
"""

from __future__ import annotations

import logging
import re
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter, DefaultFormatter

import backend.main  # noqa: F401 -- the API installs the access log filter at import
from backend.utils.logging_config import (
    SensitiveDataFilter,
    install_log_redaction,
)
from backend.utils.url_redaction import (
    INBOUND_SECRET_QUERY_PARAMS,
    REDACTED,
    redact_url_secrets,
)

CALLBACK_PATH = "/api/v1/calendar/oauth/microsoft/callback"
CALLBACK_QUERY = (
    "code=M.C507_BAY.2.U.one-time-code"
    "&state=Zm9yZ2VkLXN0YXRl"
    "&session_state=0c4e-session"
    "&scope=openid%20offline_access"
)
MASKED_CALLBACK_QUERY = (
    f"code={REDACTED}&state={REDACTED}&session_state={REDACTED}"
    "&scope=openid%20offline_access"
)
SECRETS = ("M.C507_BAY.2.U.one-time-code", "Zm9yZ2VkLXN0YXRl", "0c4e-session")

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_DIR = REPO_ROOT / "nginx"


class _Capture(logging.Handler):
    def __init__(self, formatter: logging.Formatter) -> None:
        super().__init__()
        self.setFormatter(formatter)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def capture_logger() -> Iterator:
    attached: list[tuple[logging.Logger, logging.Handler, int, bool]] = []

    def attach(name: str, formatter: logging.Formatter) -> _Capture:
        logger = logging.getLogger(name)
        handler = _Capture(formatter)
        attached.append((logger, handler, logger.level, logger.propagate))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        return handler

    yield attach

    for logger, handler, level, propagate in attached:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logger.propagate = propagate


def _access_formatter() -> AccessFormatter:
    # uvicorn's own access format, as its default logging config sets it up.
    return AccessFormatter(
        fmt=LOGGING_CONFIG["formatters"]["access"]["fmt"], use_colors=False
    )


async def _redirecting_app(scope, receive, send) -> None:
    assert scope["type"] == "http"
    await send(
        {
            "type": "http.response.start",
            "status": 303,
            "headers": [(b"location", b"/settings"), (b"content-length", b"0")],
        }
    )
    await send({"type": "http.response.body", "body": b""})


def _serve_one_request(path_and_query: str) -> None:
    """Send one request through a real uvicorn server, as the API container does."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    # log_config=None keeps uvicorn from reconfiguring the test process's logging.
    server = uvicorn.Server(
        uvicorn.Config(_redirecting_app, lifespan="off", log_config=None)
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.01)
        response = httpx.get(f"http://127.0.0.1:{port}{path_and_query}")
        assert response.status_code == 303
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def test_uvicorn_access_line_masks_the_callback_secrets(capture_logger) -> None:
    access = capture_logger("uvicorn.access", _access_formatter())

    _serve_one_request(f"{CALLBACK_PATH}?{CALLBACK_QUERY}")

    (line,) = access.lines
    assert re.fullmatch(
        r'INFO: {5}127\.0\.0\.1:\d+ - "GET '
        + re.escape(f"{CALLBACK_PATH}?{MASKED_CALLBACK_QUERY}")
        + r' HTTP/1\.1" 303 See Other',
        line,
    ), line
    for secret in SECRETS:
        assert secret not in line


def test_uvicorn_access_line_without_secrets_is_unchanged(capture_logger) -> None:
    access = capture_logger("uvicorn.access", _access_formatter())
    path = "/api/v1/recordings?status=PROCESSED&include_archived=true&tag_ids=3"

    _serve_one_request(path)

    (line,) = access.lines
    assert line.endswith(f'"GET {path} HTTP/1.1" 303 See Other'), line


def test_uvicorn_websocket_line_masks_a_token(capture_logger) -> None:
    error_log = capture_logger("uvicorn.error", DefaultFormatter("%(message)s"))

    # The call uvicorn's WebSocket protocols make when a handshake is accepted.
    logging.getLogger("uvicorn.error").info(
        '%s - "WebSocket %s" [accepted]',
        "127.0.0.1:50000",
        "/api/v1/system/logs/live?container=all&token=ws-secret",
    )

    assert error_log.lines == [
        '127.0.0.1:50000 - "WebSocket /api/v1/system/logs/live'
        f'?container=all&token={REDACTED}" [accepted]'
    ]


def test_httpx_does_not_log_each_request(caplog) -> None:
    # The URL of a calendar request holds the calendar's id, which for a Google
    # account is its address, and the provider's sync cursor.
    transport = httpx.MockTransport(lambda request: httpx.Response(200))

    with caplog.at_level(logging.INFO), httpx.Client(transport=transport) as client:
        client.get(
            "https://www.googleapis.com/calendar/v3/calendars/alice%40example.com/events",
            params={"syncToken": "cursor-secret"},
        )

    assert not [r for r in caplog.records if r.name == "httpx"]


def test_install_log_redaction_is_idempotent() -> None:
    install_log_redaction()
    install_log_redaction()

    for name in ("uvicorn.access", "uvicorn.error"):
        filters = logging.getLogger(name).filters
        assert sum(isinstance(f, SensitiveDataFilter) for f in filters) == 1, name


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            f"{CALLBACK_PATH}?{CALLBACK_QUERY}",
            f"{CALLBACK_PATH}?{MASKED_CALLBACK_QUERY}",
        ),
        (
            "/api/v1/calendar/oauth/google/callback?state=s1&code=c1&scope=x&authuser=0",
            "/api/v1/calendar/oauth/google/callback"
            f"?state={REDACTED}&code={REDACTED}&scope=x&authuser=0",
        ),
        (
            "/api/v1/calendar/webhooks/microsoft?validationToken=v1",
            f"/api/v1/calendar/webhooks/microsoft?validationToken={REDACTED}",
        ),
        (
            "/api/v1/llm/models?provider=openai&api_key=sk-1&api_url=",
            f"/api/v1/llm/models?provider=openai&api_key={REDACTED}&api_url=",
        ),
        ("/register?invite=abc123", f"/register?invite={REDACTED}"),
        (
            "/api/v1/invitations/validate/abc123",
            f"/api/v1/invitations/validate/{REDACTED}",
        ),
        (
            "https://graph.microsoft.com/v1.0/me/calendarView/delta?%24deltatoken=d1",
            "https://graph.microsoft.com/v1.0/me/calendarView/delta"
            f"?%24deltatoken={REDACTED}",
        ),
        # An empty value has nothing to hide, and names that only contain a
        # secret parameter's name are left alone.
        (
            "/api/v1/x?code=&write_token=w1&decode=d1&states=s1",
            "/api/v1/x?code=&write_token=w1&decode=d1&states=s1",
        ),
        # A URL quoted in an exception message keeps its closing quote.
        (
            "Client error '400 Bad Request' for url 'https://example.com/t?pageToken=p1'",
            "Client error '400 Bad Request' for url "
            f"'https://example.com/t?pageToken={REDACTED}'",
        ),
    ],
)
def test_redact_url_secrets(text: str, expected: str) -> None:
    assert redact_url_secrets(text) == expected


# nginx -------------------------------------------------------------------------

_MAP_RE = re.compile(r"^map\s+(\$\w+)\s+(\$\w+)\s*\{(.*?)^\}", re.M | re.S)
_ENTRY_RE = re.compile(r'^\s*(?:"((?:[^"\\]|\\.)*)"|(\S+))\s+(\S.*?);\s*$', re.M)


def _nginx_text(name: str) -> str:
    lines = (NGINX_DIR / name).read_text("utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _nginx_maps() -> dict[str, tuple[str, list[tuple[str, str]]]]:
    """Map each variable the access log config defines to its source and entries."""
    maps = {}
    for source, target, body in _MAP_RE.findall(_nginx_text("access-log.conf")):
        entries = [
            (
                entry[1] if entry[1] is not None else entry[2],
                entry[3].strip('"'),
            )
            for entry in _ENTRY_RE.finditer(body)
        ]
        maps[target] = (source, entries)
    return maps


def _nginx_value(variable: str, request_vars: dict[str, str]) -> str:
    """Evaluate one map variable the way nginx would for a request."""
    if variable in request_vars:
        return request_vars[variable]
    source, entries = _nginx_maps()[variable]
    value = _nginx_value(source, request_vars)
    default = None
    for key, result in entries:
        if key == "default":
            default = result
            continue
        if key.startswith("~"):
            flags = re.I if key.startswith("~*") else 0
            match = re.search(key.lstrip("~*"), value, flags)
            if match:
                return re.sub(r"\$(\d)", lambda m: match.group(int(m[1])), result)
        elif key == value:
            return result
    assert default is not None, variable
    return _nginx_value(default, request_vars)


def _query_maps() -> list[str]:
    return [
        key
        for _source, entries in _nginx_maps().values()
        for key, _value in entries
        if "[?&]" in key
    ]


def test_nginx_includes_the_access_log_config_in_its_http_block() -> None:
    conf = _nginx_text("nginx.conf")
    http_block = conf[conf.index("http {") : conf.index("server {")]
    assert "include /etc/nginx/certs/access-log.conf;" in http_block
    assert "access_log" not in conf

    access_log = _nginx_text("access-log.conf")
    assert "access_log /var/log/nginx/access.log nojoin_combined;" in access_log
    assert '"$nojoin_log_request"' in access_log
    assert '"$nojoin_log_referer"' in access_log


def test_nginx_masks_the_same_parameters_as_the_api() -> None:
    patterns = _query_maps()
    assert len(patterns) == 6
    assert len(set(patterns)) == 1, "every query map must use the same pattern"
    names = re.search(r"\(\?:([^)]*)\)=", patterns[0])
    assert names is not None
    assert set(names.group(1).split("|")) == INBOUND_SECRET_QUERY_PARAMS


def test_nginx_request_line_masks_the_callback_secrets() -> None:
    request = f"GET {CALLBACK_PATH}?{CALLBACK_QUERY} HTTP/1.1"
    logged = _nginx_value(
        "$nojoin_log_request", {"$request": request, "$http_referer": ""}
    )
    assert logged == f"GET {CALLBACK_PATH}?{MASKED_CALLBACK_QUERY} HTTP/1.1"


def test_nginx_masks_an_invitation_code_in_the_path_and_the_referer() -> None:
    request_vars = {
        "$request": "GET /api/v1/invitations/validate/abc123 HTTP/1.1",
        "$http_referer": "https://nojoin.example.com/register?invite=abc123",
    }
    assert (
        _nginx_value("$nojoin_log_request", request_vars)
        == f"GET /api/v1/invitations/validate/{REDACTED} HTTP/1.1"
    )
    assert (
        _nginx_value("$nojoin_log_referer", request_vars)
        == f"https://nojoin.example.com/register?invite={REDACTED}"
    )


def test_nginx_leaves_other_requests_as_combined_logs_them() -> None:
    request_vars = {
        "$request": "GET /settings?calendar=error&provider=google HTTP/1.1",
        "$http_referer": "",
    }
    assert (
        _nginx_value("$nojoin_log_request", request_vars) == (request_vars["$request"])
    )
    assert _nginx_value("$nojoin_log_referer", request_vars) == "-"
