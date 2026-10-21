"""The bundled nginx must send HSTS on every HTTPS response.

nginx copies server-level ``add_header`` directives into a location only when
that location sets none of its own. ``nginx.conf`` once set HSTS at server
level while every location set its own headers, so no response carried it.
Each location now includes ``hsts.conf`` instead, and these tests fail if a
location leaves it out or a header goes back to server level.

The web app sets its own security headers on ``/``, so nginx adds only HSTS
there. Browsers enforce every CSP they receive, so a second policy from nginx
would narrow the app's in a way nothing in ``frontend/`` shows.

Parsed as text: nothing in the test environment can run ``nginx -t``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_DIR = REPO_ROOT / "nginx"
CONF_PATH = NGINX_DIR / "nginx.conf"
HSTS_PATH = NGINX_DIR / "hsts.conf"
COMPOSE_PATH = REPO_ROOT / "docker-compose.example.yml"
DEV_GUIDE_PATH = REPO_ROOT / "docs" / "DEVELOPMENT.md"

# Compose mounts the repository's nginx/ directory here, so the include needs
# no compose change and reaches existing installs through git pull alone.
CERTS_MOUNT = "/etc/nginx/certs"
HSTS_INCLUDE = f"{CERTS_MOUNT}/hsts.conf"

_HEADER_RE = re.compile(r"^add_header\s+(\S+)")


@dataclass
class Block:
    name: str
    directives: list[str] = field(default_factory=list)
    children: list[Block] = field(default_factory=list)


def _parse(text: str) -> Block:
    """Split nginx config text into nested blocks of ``;``-ended directives."""
    text = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    root = Block("root")
    stack = [root]
    buffer = ""
    for char in text:
        if char == "{":
            child = Block(" ".join(buffer.split()))
            stack[-1].children.append(child)
            stack.append(child)
            buffer = ""
        elif char == "}":
            stack.pop()
            buffer = ""
        elif char == ";":
            stack[-1].directives.append(" ".join(buffer.split()))
            buffer = ""
        else:
            buffer += char
    assert len(stack) == 1, "unbalanced braces in nginx config"
    return root


def _https_server() -> Block:
    (http,) = [
        b for b in _parse(CONF_PATH.read_text("utf-8")).children if b.name == "http"
    ]
    servers = [
        s
        for s in http.children
        if s.name == "server" and any(d.startswith("listen 443") for d in s.directives)
    ]
    assert len(servers) == 1, "expected one HTTPS server block"
    return servers[0]


def _locations() -> dict[str, Block]:
    return {
        b.name.removeprefix("location ").strip(): b
        for b in _https_server().children
        if b.name.startswith("location")
    }


def _added_headers(block: Block) -> set[str]:
    return {m.group(1) for d in block.directives if (m := _HEADER_RE.match(d))}


def test_hsts_snippet_sends_one_year_for_this_host_only():
    directives = _parse(HSTS_PATH.read_text("utf-8")).directives
    assert directives == [
        'add_header Strict-Transport-Security "max-age=31536000" always'
    ]


def test_every_https_location_includes_hsts():
    locations = _locations()
    assert {"/api/", "/"} <= locations.keys()
    for path, block in locations.items():
        assert f"include {HSTS_INCLUDE}" in block.directives, path


def test_https_server_sets_no_header_that_locations_would_drop():
    assert not _added_headers(_https_server())


def test_api_and_mcp_locations_keep_their_own_headers():
    locations = _locations()
    common = {
        "Content-Security-Policy",
        "Referrer-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
    }
    assert _added_headers(locations["/api/"]) == common | {"Permissions-Policy"}
    (mcp,) = [b for path, b in locations.items() if "mcp" in path]
    assert _added_headers(mcp) == common


def test_app_location_leaves_security_headers_to_the_web_app():
    assert not _added_headers(_locations()["/"])


def test_compose_files_mount_the_nginx_directory_where_the_include_points():
    mount = f"./nginx:{CERTS_MOUNT}"
    assert mount in COMPOSE_PATH.read_text("utf-8")
    assert mount in DEV_GUIDE_PATH.read_text("utf-8")
