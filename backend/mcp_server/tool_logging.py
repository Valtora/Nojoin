"""Structured call logging for MCP tools.

Wraps every registered tool so each call emits one log line with the tool
name, acting user, redacted argument summary, result shape, and duration.
Redaction policy: identifiers and enums are logged verbatim, free text and
PII are summarised by length or count only, in line with the logging
redaction policy in docs/SECURITY.md.
"""

import functools
import inspect
import logging
import time
from http import HTTPStatus
from typing import Any, Callable

from mcp.server.mcpserver.exceptions import ToolError
from starlette.exceptions import HTTPException

from backend.mcp_server.auth import current_mcp_user

logger = logging.getLogger(__name__)

# Argument names safe to log verbatim: identifiers, pagination, and enums —
# never free text (note bodies, search queries) or PII (names, contact
# details), which are summarised by length/count instead. This keeps the
# MCP call log useful for debugging without recording meeting content.
_LOGGABLE_ARG_NAMES = frozenset(
    {
        "recording_id",
        "person_id",
        "task_id",
        "utterance_id",
        "calendar_event_id",
        "notes_template_id",
        "diarization_label",
        "on_conflict",
        "status",
        "sources",
        "transcription_backend",
        "max_speakers",
        "only_this_utterance",
        "completed",
        "archived",
        "expected_revision",
        "limit",
        "skip",
        "offset",
        "start_date",
        "end_date",
        "after_revision",
    }
)


def _summarise_args(arguments: dict[str, Any]) -> str:
    parts: list[str] = []
    for name, value in arguments.items():
        if value is None:
            continue
        if name in _LOGGABLE_ARG_NAMES:
            parts.append(f"{name}={value!r}")
        elif isinstance(value, str):
            parts.append(f"{name}=<str:{len(value)}>")
        elif isinstance(value, (list, tuple, dict)):
            parts.append(f"{name}=<{type(value).__name__}:{len(value)}>")
        else:
            parts.append(f"{name}=<{type(value).__name__}>")
    return " ".join(parts) or "(no args)"


def _summarise_result(result: Any) -> str:
    if isinstance(result, (list, tuple, dict)):
        return f"{type(result).__name__}:{len(result)}"
    return type(result).__name__


def _route_refusal(exc: HTTPException) -> str:
    """The text a route meant its caller to read, and nothing else.

    A plain string detail is that text. A ``{code, message}`` detail gives its
    message. Anything else falls back to the status phrase, so no structure the
    route built for another client reaches the assistant.
    """
    detail = exc.detail
    if isinstance(detail, dict):
        detail = detail.get("message")
    if isinstance(detail, str) and detail.strip():
        return detail
    try:
        return HTTPStatus(exc.status_code).phrase
    except ValueError:
        return f"Request failed ({exc.status_code})"


def logged_tool(func: Callable) -> Callable:
    """Wrap an MCP tool with structured call logging.

    Emits one INFO line per successful call (tool, user, redacted args,
    result shape, duration), a WARNING for a ToolError (an expected,
    user-facing rejection), and an EXCEPTION for anything unexpected. The
    signature is preserved via functools.wraps so the SDK still builds the
    tool's input schema from the original annotations.

    Tools call the web routes and their helpers, which refuse with an
    HTTPException. The SDK treats anything but a ToolError as a crash and
    gives the assistant only "Error executing tool <name>", so the route's
    reason is passed on here as a ToolError, for every tool. A 4xx is logged
    as a rejection. A 5xx is still a server fault, logged with its traceback;
    the assistant gets the route's detail, the same text the web app shows,
    and never the traceback or the exception behind it.
    """
    signature = inspect.signature(func)

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        user = current_mcp_user.get()
        user_id = getattr(user, "id", None)
        try:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            arg_summary = _summarise_args(bound.arguments)
        except TypeError:
            arg_summary = "<unbindable args>"
        started = time.monotonic()
        try:
            result = await func(*args, **kwargs)
        except ToolError as exc:
            logger.warning(
                "mcp tool %s rejected user=%s %s: %s",
                func.__name__,
                user_id,
                arg_summary,
                exc,
            )
            raise
        except HTTPException as exc:
            reason = _route_refusal(exc)
            if exc.status_code < 500:
                logger.warning(
                    "mcp tool %s rejected user=%s %s: %s %s",
                    func.__name__,
                    user_id,
                    arg_summary,
                    exc.status_code,
                    reason,
                )
            else:
                logger.exception(
                    "mcp tool %s failed user=%s %s: %s",
                    func.__name__,
                    user_id,
                    arg_summary,
                    exc.status_code,
                )
            raise ToolError(reason) from exc
        except Exception:
            logger.exception(
                "mcp tool %s failed user=%s %s",
                func.__name__,
                user_id,
                arg_summary,
            )
            raise
        duration_ms = (time.monotonic() - started) * 1000
        logger.info(
            "mcp tool %s ok user=%s %s -> %s (%.0fms)",
            func.__name__,
            user_id,
            arg_summary,
            _summarise_result(result),
            duration_ms,
        )
        return result

    return wrapper
