"""Every shipped proxy must flush transcript hints without an HTTP upgrade."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.tests.test_nginx_security_headers import _locations, _parse

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("config", ["nginx/nginx.conf", "demo/nginx.conf"])
def test_stream_route_flushes_hints_and_preserves_request_routing(config: str) -> None:
    root = _parse((ROOT / config).read_text(encoding="utf-8"))
    (http,) = [block for block in root.children if block.name == "http"]
    servers = [block for block in http.children if block.name == "server"]
    locations = [block for server in servers for block in server.children]
    (stream,) = [block for block in locations if "/transcripts/" in block.name]
    pattern = stream.name.removeprefix("location ~ ")
    assert re.fullmatch(pattern, "/api/v1/transcripts/recording-id/events")
    for path in (
        "/api/v1/transcripts/recording-id/utterances",
        "/api/v1/transcripts/recording-id/events/extra",
        "/api/v1/recordings/recording-id/upload-segment",
    ):
        assert re.fullmatch(pattern, path) is None
    directives = stream.directives
    assert "proxy_buffering off" in directives
    assert "proxy_cache off" in directives
    assert 'proxy_set_header Connection ""' in directives
    assert 'proxy_set_header Upgrade ""' in directives
    assert "proxy_read_timeout 300s" in directives
    # No trailing URI: the upstream receives the original public ID and query.
    assert any(re.fullmatch(r"proxy_pass \$\w+", item) for item in directives)
    for header in ("Host", "X-Forwarded-For", "X-Forwarded-Proto"):
        assert any(
            item.startswith(f"proxy_set_header {header} ") for item in directives
        )


def test_stream_location_retains_api_security_headers() -> None:
    locations = _locations()
    (stream,) = [block for path, block in locations.items() if "/transcripts/" in path]
    assert {
        item for item in locations["/api/"].directives if item.startswith("add_header ")
    } == {item for item in stream.directives if item.startswith("add_header ")}


def test_operator_proxy_template_uses_ordinary_connection_without_upgrade() -> None:
    guide = (ROOT / "docs/DEPLOYMENT.md").read_text(encoding="utf-8")
    (mapping,) = re.findall(
        r"map \$http_upgrade \$connection_upgrade \{([^}]+)\}", guide
    )
    assert "default upgrade;" in mapping
    assert "''      '';" in mapping
    example = guide.split("### Nginx Example", 1)[1].split("### Tailscale Serve", 1)[0]
    assert "proxy_buffering off;" in example
    assert "proxy_cache off;" in example
    assert "proxy_read_timeout 300s;" in example
