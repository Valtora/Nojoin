"""Running the nojoin-proton helper: one subprocess per Proton operation.

The helper (``proton/``, contract in ``proton/PROTOCOL.md``) speaks Proton's
private API and iCalendar; this module only moves one JSON request in and one
JSON response out. Secrets travel on stdin alone: never in argv or the
environment, which is an allowlist, and never in a log line. A helper that
crashes, overruns its budget or answers with something that is not one JSON
object is ``provider_unavailable``, with ``outcome_unknown`` for a write. The
whole process group is killed on a timeout. At most four helpers run at once
in one process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import weakref
from typing import Any

from backend.utils.version import get_installed_version

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
DEFAULT_HELPER_PATH = "/usr/local/bin/nojoin-proton"
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
WRITE_TIMEOUT_SECONDS = 30.0
READ_TIMEOUT_SECONDS = 30.0
SYNC_TIMEOUT_SECONDS = 120.0
# Proton's refresh tokens are single use, so a refresh waits for its answer
# (the helper allows 90 s); the kill comes after that.
REFRESH_TIMEOUT_SECONDS = 100.0
_HELPER_MARGIN_SECONDS = 2.0
_ENVIRONMENT_ALLOWLIST = ("HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE")
WRITE_OPERATIONS = frozenset(
    {"create_event", "update_event", "cancel_event", "respond", "send_itip"}
)
_HELPERS_AT_ONCE = 4
# One semaphore per event loop: a Celery task runs each job in a loop of its
# own, and an asyncio primitive cannot be shared across loops.
_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _semaphores.get(loop)
    if semaphore is None:
        semaphore = _semaphores[loop] = asyncio.Semaphore(_HELPERS_AT_ONCE)
    return semaphore


class HelperError(Exception):
    """The helper's ``{"ok": false}`` answer, or a crash described the same way."""

    def __init__(  # noqa: PLR0913 - one keyword per field of the helper's error
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        provider_code: int | None = None,
        http_status: int | None = None,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.provider_code = provider_code
        self.http_status = http_status
        self.field = field
        self.details = details or {}


def helper_path() -> str:
    """The helper binary; NOJOIN_PROTON_HELPER points tests at a fake."""
    return os.getenv("NOJOIN_PROTON_HELPER") or DEFAULT_HELPER_PATH


def api_base_url() -> str | None:
    """An operator or test override of Proton's API URL; never user input."""
    return os.getenv("NOJOIN_PROTON_API_BASE_URL") or None


def _environment() -> dict[str, str]:
    return {
        name: os.environ[name] for name in _ENVIRONMENT_ALLOWLIST if name in os.environ
    }


def _crash(op: str, message: str) -> HelperError:
    return HelperError(
        "provider_unavailable",
        message,
        retryable=True,
        details={"outcome_unknown": op in WRITE_OPERATIONS},
    )


def _kill(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _run(op: str, payload: bytes, timeout: float) -> bytes:
    process = await asyncio.create_subprocess_exec(
        helper_path(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_environment(),
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout)
    except TimeoutError as exc:
        _kill(process)
        await process.wait()
        raise _crash(op, "Proton did not answer in time.") from exc
    if process.returncode != 0:
        # stderr names at most an error code; keep only its first word.
        code = (stderr.decode(errors="replace").split() or ["crash"])[0][:64]
        logger.warning("nojoin-proton %s exited %s (%s)", op, process.returncode, code)
        raise _crash(op, "The Proton helper stopped unexpectedly.")
    if len(stdout) > MAX_OUTPUT_BYTES:
        raise _crash(op, "The Proton helper's answer was too large.")
    return stdout


async def call_helper(
    op: str,
    *,
    session: dict[str, Any] | None = None,
    args: dict[str, Any] | None = None,
    timeout: float = READ_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run one helper operation and return its ``result``, or raise HelperError."""
    request = {
        "v": PROTOCOL_VERSION,
        "op": op,
        "session": session or {},
        "client": {
            "user_agent": f"Nojoin/{get_installed_version()}",
            "timeout_ms": int(max(timeout - _HELPER_MARGIN_SECONDS, 1.0) * 1000),
            "api_base_url": api_base_url(),
        },
        "args": args or {},
    }
    payload = json.dumps(request).encode()
    async with _semaphore():
        try:
            stdout = await _run(op, payload, timeout)
        except OSError as exc:
            raise _crash(op, "The Proton helper could not be started.") from exc
    try:
        response = json.loads(stdout)
    except ValueError as exc:
        raise _crash(op, "The Proton helper's answer was not JSON.") from exc
    if not isinstance(response, dict) or response.get("v") != PROTOCOL_VERSION:
        raise _crash(op, "The Proton helper answered in an unknown shape.")
    if response.get("ok"):
        result = response.get("result")
        return result if isinstance(result, dict) else {}
    error = response.get("error") if isinstance(response.get("error"), dict) else {}
    raise HelperError(
        str(error.get("code") or "provider_unavailable"),
        str(error.get("message") or "Proton refused the request."),
        retryable=bool(error.get("retryable")),
        provider_code=error.get("provider_code"),
        http_status=error.get("http_status"),
        field=error.get("field"),
        details=error.get("details") if isinstance(error.get("details"), dict) else {},
    )
