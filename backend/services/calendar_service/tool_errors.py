"""The refusals calendar tools give, as one JSON object an agent can act on.

A calendar tool refuses with ``CalendarToolError``; the MCP layer turns it into
a ToolError whose text is ``{"error": {...}}``, so every MCP client passes the
code, the field and the details through unchanged. The codes are fixed (the
table in docs/MCP.md), and ``retryable`` follows from the code unless a raise
says otherwise.

No MCP or web framework import here: the worker image imports the calendar
service.
"""

from __future__ import annotations

import json
from typing import Any

# code -> whether the same call can succeed later without a change.
ERROR_CODES: dict[str, bool] = {
    "invalid_argument": False,
    "invalid_time": False,
    "ambiguous_time": False,
    "scope_required": False,
    "unsupported": False,
    "not_found": False,
    "not_writable": False,
    "not_organiser": False,
    "not_attendee": False,
    "conflict": False,
    "idempotency_conflict": False,
    "write_scope_missing": False,
    "auth_expired": False,
    "rate_limited": True,
    "busy": True,
    "provider_unavailable": True,
    "provider_rejected": False,
}


class CalendarToolError(Exception):
    """A calendar tool's refusal: a fixed code, a message for the agent, and
    optionally the field at fault and structured details.

    ``details`` may carry addresses, so only the code is ever logged.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"Unknown calendar error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.details = details or {}
        self.retryable = ERROR_CODES[code] if retryable is None else retryable

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "field": self.field,
                "details": self.details,
            }
        }

    def to_json(self) -> str:
        return json.dumps(self.payload(), ensure_ascii=False, default=str)

    def log_summary(self) -> str:
        """What the call log keeps: the code and the field, never the details."""
        return f"{self.code}" + (f" field={self.field}" if self.field else "")
