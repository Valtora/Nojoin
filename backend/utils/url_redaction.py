"""Mask secrets and one-time values that URLs carry into the logs.

uvicorn's access log and WebSocket lines print each request URL with its query
string, and the setup account can download those logs from Settings. Some URLs
carry a value that must not reach a log a user may paste into a bug report: the
calendar sign-in callback's ``code``, ``state`` and ``session_state``, an
invitation code, an API key. Only the value is masked, so the path and every
other parameter stay as they were.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

REDACTED = "<redacted>"

# Parameters masked in requests that reach Nojoin, compared case-insensitively.
# The bundled nginx masks the same names in its own access log
# (nginx/access-log.conf), and test_access_log_redaction keeps the two equal.
INBOUND_SECRET_QUERY_PARAMS = frozenset(
    {
        # An OAuth authorisation response: the calendar sign-in callbacks, and
        # the state an MCP client passes through the consent page.
        "code",
        "state",
        "session_state",
        # Credentials a client could send in a query string. Nojoin's own
        # clients send them in headers, cookies or bodies.
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "ticket",
        "client_secret",
        "api_key",
        # Microsoft Graph's subscription validation call.
        "validationtoken",
        # An invitation link, /register?invite=<code>.
        "invite",
        "invite_code",
    }
)

# Parameters of the requests Nojoin sends, masked where an error message quotes
# the URL: the calendar providers' sync and paging cursors.
OUTBOUND_SECRET_QUERY_PARAMS = frozenset(
    {"synctoken", "pagetoken", "$deltatoken", "$skiptoken"}
)

SECRET_QUERY_PARAMS = INBOUND_SECRET_QUERY_PARAMS | OUTBOUND_SECRET_QUERY_PARAMS

# A non-empty parameter value runs to the next separator, fragment, space or
# quote, so a URL quoted inside a longer message keeps its closing quote.
_QUERY_PARAM = re.compile(r"(?<=[?&])([^=&#\s\"']+)=([^&#\s\"']+)")

# The invitation check takes the code in its path, as
# /api/v1/invitations/validate/<code>.
_INVITATION_CODE_PATH = re.compile(r"(/invitations/validate/)[^/?#\s\"']+")


def _mask_param(match: re.Match[str]) -> str:
    name = match.group(1)
    if unquote(name).lower() in SECRET_QUERY_PARAMS:
        return f"{name}={REDACTED}"
    return match.group(0)


def redact_url_secrets(text: str) -> str:
    """Return ``text`` with the values of secret URL parameters masked."""
    if "=" in text:
        text = _QUERY_PARAM.sub(_mask_param, text)
    if "/invitations/validate/" in text:
        text = _INVITATION_CODE_PATH.sub(rf"\g<1>{REDACTED}", text)
    return text
