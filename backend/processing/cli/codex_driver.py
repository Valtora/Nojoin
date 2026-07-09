"""Codex-exec driver for CLI OAuth inference (ChatGPT subscription).

The Codex counterpart to the Claude Agent SDK path in
:mod:`backend.processing.cli.manager`. There is no first-party Python SDK for the
subscription path, so this drives the ``codex`` binary as a single-turn,
read-only subprocess (confirmed in the CX-0 spike):

    codex exec --json --ephemeral --skip-git-repo-check --ignore-user-config \
        -s read-only -C <workdir> -o <last_message_file> [-m <model>] -

The subscription token is injected per call via ``codex login
--with-access-token`` (which writes a valid ``auth.json`` into a per-user
``CODEX_HOME`` — so Nojoin never has to reproduce that file's schema), and the
child environment is built by :func:`codex_child_env` with the OpenAI key-auth
vars removed so Codex bills the subscription, not an API key.

Only imported when a user actually routes through Codex (the manager imports this
module lazily), so the Claude path never loads it.

VERIFY before release (assumed working per the CX-0 decision, proven later like
Claude): the exact ``--json`` event schema and the ``--with-access-token`` token
shape. Parsing is deliberately tolerant and always falls back to the reliable
``-o`` last-message file for text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator, Optional

# Intra-package imports of the manager's shared inference types/helpers. Safe from
# a cycle because the manager only imports this module lazily (inside a method),
# so the manager is fully loaded by the time this import runs.
from backend.processing.cli.env_scrub import codex_child_env
from backend.processing.cli.manager import (
    CliOAuthUnavailableError,
    _TurnUsage,
    _usage_limit_error,
)
from backend.services.cli_oauth.persistence import user_cli_dir

logger = logging.getLogger(__name__)

# Absolute path to the codex binary in the worker-io image (CX-6 installs it).
CODEX_PATH = os.environ.get("NOJOIN_CODEX_PATH", "/usr/local/bin/codex")
DEFAULT_TIMEOUT_SECONDS = 300

_RATE_LIMIT_TOKENS = (
    "429",
    "rate limit",
    "rate_limit",
    "usage limit",
    "quota",
    "too many requests",
)


class CodexExecDriver:
    """Drives single-turn ``codex exec`` inference for one user at a time.

    Stateless across calls (a fresh subprocess per call). Mirrors the async
    driver contract the manager expects from the Claude path:
    ``arun_single_turn`` returns ``(text, _TurnUsage)``; ``astream_single_turn``
    yields text and appends the turn's usage to ``usage_sink``.
    """

    def __init__(self, provider: str) -> None:
        self._provider = provider

    async def arun_single_turn(
        self,
        user_id: int,
        prompt: str,
        access_token: str,
        *,
        model: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[str, _TurnUsage]:
        codex_home = self._prepare_dir(user_id, "codex")
        workdir = self._prepare_dir(user_id, "codex-work")
        self._inject_auth(codex_home, access_token)

        last_message = workdir / "last_message.txt"
        # Clear any prior turn's output so a failed write can't read as success.
        last_message.unlink(missing_ok=True)

        args = self._exec_args(model, workdir, last_message)
        env = codex_child_env(str(codex_home))
        return_code, stdout, stderr = await self._run(args, prompt, env, timeout)
        if return_code != 0:
            raise self._classify_error(stderr)

        text = self._read_last_message(last_message) or _text_from_jsonl(stdout)
        text = text.strip()
        if not text:
            raise CliOAuthUnavailableError("Codex inference returned no text.")
        return text, _TurnUsage(usage=_usage_from_jsonl(stdout))

    async def astream_single_turn(  # noqa: PLR0913 - cohesive driver params
        self,
        user_id: int,
        prompt: str,
        access_token: str,
        model: Optional[str],
        timeout: int,
        usage_sink: list[_TurnUsage],
    ) -> AsyncIterator[str]:
        """Yield the answer for one turn.

        Codex `--json` emits JSONL whose per-token delta shape varies by version,
        so v1 yields the completed answer as a single chunk (from the reliable
        ``-o`` file) rather than risk mis-parsing deltas. True token streaming is
        a follow-up once the event schema is verified end-to-end.
        """
        text, turn = await self.arun_single_turn(
            user_id, prompt, access_token, model=model, timeout=timeout
        )
        usage_sink.append(turn)
        if text:
            yield text

    # ---- subprocess helpers ----

    def _exec_args(
        self, model: Optional[str], workdir: Path, last_message: Path
    ) -> list[str]:
        args = [
            CODEX_PATH,
            "exec",
            "--json",  # JSONL events on stdout (usage parsing)
            "--ephemeral",  # no session rollout files on disk
            "--skip-git-repo-check",  # inference-only; not a git workspace
            "--ignore-user-config",  # hermetic; auth still read from CODEX_HOME
            "-s",
            "read-only",  # tools-off posture: no writes, no shell
            "-C",
            str(workdir),
            "-o",
            str(last_message),  # final agent message → file (reliable text)
        ]
        if model:
            args += ["-m", model]
        args += ["-"]  # read the prompt from stdin
        return args

    def _inject_auth(self, codex_home: Path, auth_blob: str) -> None:
        """Materialise the stored ``auth.json`` into CODEX_HOME.

        For Codex the manager passes the full ``auth.json`` blob (captured at
        connect via ``codex login --device-auth``) as the credential, so writing
        it verbatim reproduces exactly what the CLI expects. The manager
        re-persists any CLI-refreshed copy and wipes this plaintext after the
        turn (encrypted-at-rest)."""
        auth_path = codex_home / "auth.json"
        auth_path.write_text(auth_blob, encoding="utf-8")
        try:
            os.chmod(auth_path, 0o600)
        except OSError:  # best-effort; exotic filesystems may reject chmod
            pass

    async def _run(
        self, args: list[str], stdin_text: str, env: dict[str, str], timeout: int
    ) -> tuple[int, str, str]:
        """Run a codex subprocess, feeding ``stdin_text``; return (rc, out, err).

        Uses ``communicate`` so stdout and stderr are drained concurrently (codex
        streams progress to stderr — a plain unread PIPE could deadlock)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise CliOAuthUnavailableError(
                f"Codex CLI not found at {CODEX_PATH}."
            ) from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_text.encode()), timeout=timeout
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            proc.kill()
            await proc.wait()
            raise CliOAuthUnavailableError(
                f"Codex inference timed out after {timeout}s."
            ) from exc
        return (
            proc.returncode or 0,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )

    def _classify_error(self, stderr: str) -> CliOAuthUnavailableError:
        """Map a non-zero exit to a usage-limit or generic unavailable error.

        A subscription exposes no structured reset time (unlike the Claude SDK's
        RateLimitEvent), so a detected limit carries no ``resets_at``."""
        lowered = (stderr or "").lower()
        if any(token in lowered for token in _RATE_LIMIT_TOKENS):
            return _usage_limit_error(None, None)
        detail = (stderr or "").strip()[:300] or "unknown error"
        return CliOAuthUnavailableError(f"Codex inference failed: {detail}")

    @staticmethod
    def _prepare_dir(user_id: int, name: str) -> Path:
        path = user_cli_dir(user_id) / name
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:  # best-effort; exotic filesystems may reject chmod
            pass
        return path

    @staticmethod
    def _read_last_message(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


# ---- tolerant JSONL parsing (schema verified later; see module docstring) ----


def _iter_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def _text_from_jsonl(stdout: str) -> str:
    """Best-effort assembled agent text from JSONL, if the -o file was empty."""
    parts = [_event_text(event) for event in _iter_events(stdout)]
    return "".join(part for part in parts if part)


def _event_text(event: dict[str, Any]) -> str:
    """Extract agent text from one event across known codex --json shapes."""
    item = event.get("item")
    if isinstance(item, dict) and item.get("type") in (
        "agent_message",
        "assistant_message",
    ):
        value = item.get("text") or item.get("message")
        if isinstance(value, str):
            return value
    msg = event.get("msg")
    if isinstance(msg, dict) and msg.get("type") in (
        "agent_message",
        "agent_message_delta",
    ):
        value = msg.get("message") or msg.get("text") or msg.get("delta")
        if isinstance(value, str):
            return value
    return ""


def _usage_from_jsonl(stdout: str) -> Optional[dict[str, int]]:
    """Latest token-usage reading from the JSONL stream, mapped to the rollup's
    Anthropic-shaped keys. None when no usage event is present."""
    usage: Optional[dict[str, int]] = None
    for event in _iter_events(stdout):
        found = _event_usage(event)
        if found is not None:
            usage = found  # keep the last (turn-final) reading
    return usage


def _event_usage(event: dict[str, Any]) -> Optional[dict[str, int]]:
    for container in (event, event.get("msg"), event.get("item"), event.get("info")):
        if not isinstance(container, dict):
            continue
        raw = container.get("usage")
        if isinstance(raw, dict):
            return _normalise_usage(raw)
        if any(
            key in container
            for key in ("input_tokens", "output_tokens", "total_tokens")
        ):
            return _normalise_usage(container)
    return None


def _normalise_usage(raw: dict[str, Any]) -> dict[str, int]:
    def pick(*keys: str) -> int:
        for key in keys:
            value = raw.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        return 0

    return {
        "input_tokens": pick("input_tokens", "prompt_tokens", "input"),
        "output_tokens": pick("output_tokens", "completion_tokens", "output"),
        "cache_read_input_tokens": pick(
            "cached_input_tokens", "cache_read_input_tokens", "cache_read"
        ),
        "cache_creation_input_tokens": pick("cache_creation_input_tokens"),
    }
