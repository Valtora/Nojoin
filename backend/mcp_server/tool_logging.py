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
from typing import Any, Callable

from mcp.server.fastmcp.exceptions import ToolError

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
        "diarization_label",
        "on_conflict",
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


def logged_tool(func: Callable) -> Callable:
    """Wrap an MCP tool with structured call logging.

    Emits one INFO line per successful call (tool, user, redacted args,
    result shape, duration), a WARNING for a ToolError (an expected,
    user-facing rejection), and an EXCEPTION for anything unexpected. The
    signature is preserved via functools.wraps so FastMCP still builds the
    tool's input schema from the original annotations.
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
