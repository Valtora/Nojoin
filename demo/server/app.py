"""The API behind the public demo: captured responses, replayed read-only.

There is no database, no worker and no credential in this process. Every
response it can give was captured from a seeded Nojoin instance by
``demo/capture/capture.mjs`` and sits in the fixture directory. Reads are
matched against those captures. Writes are refused, apart from signing in and
out, which only set and clear a cookie so the frontend's own session handling
works unchanged.

Matching is exact on the path and the sorted query string first, then on the
path alone. The second step covers requests whose query the capture never saw;
those get the captured answer for the same path, so a panel shows sample data
instead of an error. A search the capture never ran is narrowed to the items
that mention the term.

Captured data would otherwise age: a task due in two days reads as overdue a
month later, and the calendar empties. So every ISO date in a JSON response is
moved forward by the whole weeks that have passed since the capture, and dates
in the query string are moved back by the same amount before matching. Whole
weeks keep each meeting on its original weekday.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

FIXTURES = Path(os.environ.get("DEMO_FIXTURES", "/fixtures"))
SESSION_COOKIE = "access_token"
API_PREFIX = "/api/v1"

READ_ONLY = "This is a read-only demo, so changes aren't saved."

# Answered before signing in, as on a real install, because the frontend asks
# for them while deciding whether to show the sign-in page.
PUBLIC_PATHS = frozenset(
    {
        f"{API_PREFIX}/system/health",
        f"{API_PREFIX}/system/status",
        f"{API_PREFIX}/version",
    }
)

LOGIN_METADATA = {
    "force_password_change": False,
    "is_superuser": False,
    "username": "demo",
}

# A date, alone or leading an ISO datetime. Only whole strings match, so a date
# written inside a sentence of meeting notes is left as it was said.
ISO_DATE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})((?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?)$"
)


def shift_string(value: str, days: int) -> str:
    match = ISO_DATE.match(value)
    if not match:
        return value
    try:
        moved = date.fromisoformat(match.group(1)) + timedelta(days=days)
    except ValueError:
        return value
    return moved.isoformat() + match.group(2)


def shift_dates(value: Any, days: int) -> Any:
    if isinstance(value, str):
        return shift_string(value, days)
    if isinstance(value, list):
        return [shift_dates(item, days) for item in value]
    if isinstance(value, dict):
        return {shift_string(k, days): shift_dates(v, days) for k, v in value.items()}
    return value


def canonical_query(query: str, days: int = 0) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    return urlencode(sorted((k, shift_string(v, days)) for k, v in pairs))


class Fixtures:
    def __init__(self, root: Path) -> None:
        manifest = json.loads((root / "index.json").read_text(encoding="utf-8"))
        self.root = root
        self.captured_on = date.fromisoformat(manifest["captured_on"])
        self.exact: dict[tuple[str, str], dict] = {}
        self.by_path: dict[str, dict] = {}
        for entry in manifest["entries"]:
            query = canonical_query(entry.get("query", ""))
            self.exact[(entry["path"], query)] = entry
            # The capture with the shortest query is the unfiltered request,
            # and the most useful stand-in for a query never captured.
            current = self.by_path.get(entry["path"])
            if current is None or len(query) < len(
                canonical_query(current.get("query", ""))
            ):
                self.by_path[entry["path"]] = entry

    def offset_days(self, today: date | None = None) -> int:
        elapsed = ((today or date.today()) - self.captured_on).days
        return max(0, elapsed // 7 * 7)

    def find(self, path: str, query: str, days: int) -> tuple[dict | None, str]:
        """Return the matching capture, and a search term to apply to it.

        The term is set only when the answer is the path-only stand-in for a
        search the capture never ran, so the list can at least be narrowed.
        """
        entry = self.exact.get((path, canonical_query(query, -days)))
        if entry is not None:
            return entry, ""
        term = dict(parse_qsl(query)).get("q", "").strip()
        return self.by_path.get(path), term


def narrow(payload: Any, term: str) -> Any:
    """Keep the list items that mention the term anywhere in their fields."""
    needle = term.casefold()

    def keep(items: list) -> list:
        return [
            item
            for item in items
            if needle in json.dumps(item, ensure_ascii=False).casefold()
        ]

    if isinstance(payload, list):
        return keep(payload)
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = keep(payload["items"])
        return {
            **payload,
            "items": items,
            **({"total": len(items)} if "total" in payload else {}),
        }
    return payload


FIXTURE_SET = Fixtures(FIXTURES)


@lru_cache(maxsize=512)
def shifted_body(body: str, days: int) -> Any:
    return shift_dates(json.loads((FIXTURES / body).read_text(encoding="utf-8")), days)


def replay(entry: dict, days: int, term: str = "") -> Response:
    status = entry.get("status", 200)
    headers = {"cache-control": "no-store"}
    if entry.get("content_disposition"):
        headers["content-disposition"] = entry["content_disposition"]
    content_type = entry.get("content_type", "application/json")
    if content_type.startswith("application/json"):
        payload = shifted_body(entry["body"], days)
        if term:
            payload = narrow(payload, term)
        return JSONResponse(payload, status_code=status, headers=headers)
    # FileResponse answers Range requests, which audio seeking depends on.
    return FileResponse(
        FIXTURES / entry["body"],
        status_code=status,
        media_type=content_type,
        headers=headers,
    )


async def login(request: Request) -> Response:
    response = JSONResponse(LOGIN_METADATA)
    response.set_cookie(
        SESSION_COOKIE,
        "demo",
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


async def logout(request: Request) -> Response:
    response = JSONResponse({"detail": "Signed out"})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


async def api(request: Request) -> Response:
    if request.method not in ("GET", "HEAD"):
        return JSONResponse({"detail": READ_ONLY}, status_code=403)

    path = request.url.path.rstrip("/") or "/"
    if path not in PUBLIC_PATHS and SESSION_COOKIE not in request.cookies:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)

    days = FIXTURE_SET.offset_days()
    entry, term = FIXTURE_SET.find(path, request.url.query, days)
    if entry is None:
        return JSONResponse({"detail": "Not available in the demo"}, status_code=404)
    return replay(entry, days, term)


app = Starlette(
    routes=[
        Route(f"{API_PREFIX}/login/session", login, methods=["POST"]),
        Route(f"{API_PREFIX}/login/logout", logout, methods=["POST"]),
        Route(
            "/api/{rest:path}",
            api,
            methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
        ),
    ]
)
