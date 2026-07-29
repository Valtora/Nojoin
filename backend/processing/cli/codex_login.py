"""Drive ``codex login --device-auth`` under a pty and capture the device flow.

The Codex CLI renders device-code login as a TUI: it prints the verification URL
and one-time code only when attached to a real terminal (suppressed on a bare
pipe, which is why an httpx reimplementation of the endpoints fails). So this
runs the CLI under a pseudo-terminal, scrapes the URL + code
for the API to surface, and blocks until the user approves in a browser and the
CLI writes ``auth.json`` (or the code lapses / times out). It lets the CLI own
OpenAI's undocumented ``/deviceauth`` protocol end to end.

worker-io only (the ``codex`` binary and a pty live there); driven by
``codex_device_login_task``.
"""

from __future__ import annotations

import logging
import os
import pty
import re
import select
import signal
import time
from typing import Callable, Optional

from backend.processing.cli.env_scrub import codex_child_env
from backend.services.cli_oauth.persistence import user_cli_dir

logger = logging.getLogger(__name__)

# Path to the codex binary (kept in sync with codex_driver.CODEX_PATH; duplicated
# as a literal to avoid importing the driver — and its manager import — here).
CODEX_PATH = os.environ.get("NOJOIN_CODEX_PATH", "/usr/local/bin/codex")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_URL = re.compile(r"https://auth\.openai\.com/\S*device\S*")
_CODE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4,6})\b")
_DEFAULT_TIMEOUT_SECONDS = 900  # the code's stated ~15-minute lifetime
_READ_CHUNK = 4096
_SELECT_TIMEOUT = 2.0


class CodexLoginError(RuntimeError):
    """The device login did not complete (declined, timed out, or CLI error)."""


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def codex_home_for(user_id: int):
    """The per-user CODEX_HOME (where auth.json is written)."""
    return user_cli_dir(user_id) / "codex"


def run_device_login(  # noqa: C901, PLR0912, PLR0915 - cohesive pty read/parse/wait loop
    user_id: int,
    on_code: Callable[[str, str], None],
    timeout: int = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Run the device login for ``user_id``.

    Calls ``on_code(verification_uri, user_code)`` once the CLI prints them, then
    blocks until the CLI writes ``auth.json`` (user approved) and returns its raw
    contents. Raises :class:`CodexLoginError` on decline/timeout/failure.
    """
    codex_home = codex_home_for(user_id)
    codex_home.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(codex_home, 0o700)
    except OSError:  # best-effort; exotic filesystems may reject chmod
        pass
    env = codex_child_env(str(codex_home))

    pid, master_fd = pty.fork()
    if pid == 0:  # child: become the codex CLI under the pty
        try:
            os.execvpe(CODEX_PATH, [CODEX_PATH, "login", "--device-auth"], env)
        except Exception:  # noqa: BLE001 - exec failure in the forked child
            os._exit(127)

    buffer = ""
    reported = False
    exit_code: Optional[int] = None
    start = time.monotonic()
    try:
        while True:
            if time.monotonic() - start > timeout:
                _terminate(pid)
                raise CodexLoginError("ChatGPT sign-in timed out.")
            try:
                ready, _, _ = select.select([master_fd], [], [], _SELECT_TIMEOUT)
            except OSError:
                break
            if ready:
                try:
                    data = os.read(master_fd, _READ_CHUNK)
                except OSError:
                    data = b""  # pty closed → child exited
                if not data:
                    break
                buffer += _strip_ansi(data.decode(errors="replace"))
                if not reported:
                    url = _URL.search(buffer)
                    code = _CODE.search(buffer)
                    if url and code:
                        reported = True
                        try:
                            on_code(url.group(0), code.group(1))
                        except Exception:  # noqa: BLE001 - publish is best-effort
                            logger.exception("codex login: on_code publish failed")
            reaped_pid, status = os.waitpid(pid, os.WNOHANG)
            if reaped_pid == pid:
                exit_code = os.waitstatus_to_exitcode(status)
                break
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass

    if exit_code is None:
        _, status = os.waitpid(pid, 0)
        exit_code = os.waitstatus_to_exitcode(status)
    if exit_code != 0:
        raise CodexLoginError(f"ChatGPT sign-in did not complete (exit {exit_code}).")

    try:
        return (codex_home / "auth.json").read_text(encoding="utf-8")
    except OSError as exc:
        raise CodexLoginError("ChatGPT sign-in produced no credentials.") from exc


def _terminate(pid: int) -> None:
    """Best-effort SIGTERM then SIGKILL, and reap, so no codex process lingers."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except OSError:
            return
        try:
            for _ in range(10):
                reaped, _ = os.waitpid(pid, os.WNOHANG)
                if reaped == pid:
                    return
                time.sleep(0.1)
        except OSError:
            return
