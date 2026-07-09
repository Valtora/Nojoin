"""Async driver over the Claude Agent SDK for CLI OAuth inference.

Runs the Claude Code CLI as a single-turn, tools-off subprocess authenticated by
the user's subscription OAuth token (env var ``CLAUDE_CODE_OAUTH_TOKEN``). Only
imported inside the ``worker-io`` image, where Node + the CLI + ``claude_agent_sdk``
are installed; the SDK is imported lazily inside the call methods so the API and
the gpu/cpu worker images never load it.

Token I/O is synchronous (the worker's native mode): the credential is read and
persisted with a sync session, and the two genuinely-async operations — the httpx
refresh and the SDK query — are each wrapped in their own ``asyncio.run``. Neither
touches the shared async DB engine, so there is no asyncpg cross-event-loop pool
hazard from running one ``asyncio.run`` per task.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

from sqlmodel import Session, select

from backend.core.db import get_sync_session
from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.cli_oauth import (
    CliOAuthCredential,
    CliOAuthCredentialStatus,
    CliOAuthProvider,
    CliUsageDaily,
)
from backend.processing.cli.env_scrub import scrubbed_environ, subscription_env_payload
from backend.services.cli_oauth import codex_oauth, oauth
from backend.services.cli_oauth.persistence import user_cli_dir
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)

CLI_PATH = "/usr/bin/claude"
DEFAULT_TIMEOUT_SECONDS = 300
_REFRESH_SKEW_SECONDS = 120

# A tight, non-agentic system prompt so the CLI behaves as a plain inference
# endpoint rather than a coding agent. A plain string fully replaces the CLI's
# large built-in preset (~23k tokens observed), conserving subscription quota and
# keeping outputs on-contract.
_SYSTEM_PROMPT = (
    "You are a precise text-processing engine embedded in an application. "
    "Follow the user's instructions exactly and return only the requested "
    "output — no preamble, no commentary, no questions. When the instructions "
    "ask for JSON, return only valid JSON."
)


@dataclass
class _RateLimitReading:
    """Latest-known rate-limit status from a ``RateLimitEvent`` (any status).

    ``utilization`` is the fraction (0.0-1.0) of the current window consumed; the
    CLI emits the event only on status transitions, so this is opportunistic.
    """

    status: Optional[str]
    rate_limit_type: Optional[str]
    utilization: Optional[float]


@dataclass
class _TurnUsage:
    """Token usage from a turn's terminal ``ResultMessage`` plus any rate-limit
    reading seen while draining the query. Threaded out of the async drivers so
    the sync entry points persist it (the drivers keep no DB work of their own)."""

    usage: Optional[dict[str, Any]] = None
    total_cost_usd: Optional[float] = None
    reading: Optional[_RateLimitReading] = None

    @property
    def has_data(self) -> bool:
        return self.usage is not None or self.reading is not None


class CliOAuthUnavailableError(RuntimeError):
    """CLI OAuth inference could not proceed (no active credential, a failed
    refresh, or an SDK/CLI error). ``SecondaryLLMBackend`` degrades to the user's
    configured fallback when one exists; otherwise it surfaces to the caller."""


class CliUsageLimitError(CliOAuthUnavailableError):
    """The subscription hit a usage/rate limit. Carries the best-effort reset time
    (from the SDK's ``RateLimitEvent``) so callers can tell the user when it lifts.
    Subclasses ``CliOAuthUnavailableError`` so ``SecondaryLLMBackend`` still
    degrades to the user's configured fallback."""

    def __init__(
        self,
        message: str,
        *,
        resets_at: Optional[datetime] = None,
        rate_limit_type: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.resets_at = resets_at
        self.rate_limit_type = rate_limit_type


class CliConversationManager:
    """Drives single-turn subscription-CLI inference for one provider.

    Owns the provider-neutral token lifecycle (resolve/refresh) and usage
    persistence, and dispatches the actual inference to the matching driver: the
    inline Claude Agent SDK path (below) or the Codex-exec driver. Stateless
    across calls (a fresh session per call): async tasks, chat, and live Meeting
    Edge all use a fresh single-turn query (Edge carries context via its bounded
    rolling summary, not a resumable session).
    """

    def __init__(self, provider: str = CliOAuthProvider.CLAUDE_CODE.value) -> None:
        self._provider = provider
        # Built lazily on first use (only for Codex) so the Claude path never
        # imports the codex-exec subprocess plumbing.
        self._codex_driver = None

    def _is_codex(self) -> bool:
        return self._provider == CliOAuthProvider.CODEX.value

    def _codex(self):
        if self._codex_driver is None:
            from backend.processing.cli.codex_driver import CodexExecDriver

            self._codex_driver = CodexExecDriver(self._provider)
        return self._codex_driver

    def _astream_for(  # noqa: PLR0913 - cohesive driver params
        self,
        user_id: int,
        prompt: str,
        access_token: str,
        model: Optional[str],
        timeout: int,
        usage_sink: list[_TurnUsage],
    ):
        """Pick the provider's streaming driver (Codex vs the inline Claude path)."""
        if self._is_codex():
            return self._codex().astream_single_turn(
                user_id, prompt, access_token, model, timeout, usage_sink
            )
        return self._astream_single_turn(
            user_id, prompt, access_token, model, timeout, usage_sink
        )

    def _finalize_codex_auth(self, user_id: int, injected_blob: str) -> None:
        """After a codex exec: persist the (possibly CLI-refreshed) auth.json and
        wipe the plaintext, so the token stays encrypted at rest and any rotation
        survives. No-op for non-codex providers. Best-effort — never breaks
        inference."""
        if not self._is_codex():
            return
        from backend.processing.cli.codex_login import codex_home_for
        from backend.services.cli_oauth.persistence import store_codex_auth_blob_sync

        auth_path = codex_home_for(user_id) / "auth.json"
        try:
            current = auth_path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            if current and current != injected_blob:
                store_codex_auth_blob_sync(user_id, current)
        except Exception:  # noqa: BLE001 - persistence must not break inference
            logger.exception("Failed to persist refreshed Codex auth for %s", user_id)
        finally:
            try:
                auth_path.unlink(missing_ok=True)
            except OSError:
                pass

    # ---- public sync entry points (called from the sync worker) ----

    def run_single_turn(
        self,
        user_id: int,
        prompt: str,
        *,
        model: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> str:
        """Resolve a fresh token, run one non-streaming turn, return the text."""
        access_token = self._resolve_access_token(user_id)
        try:
            if self._is_codex():
                text, usage = asyncio.run(
                    self._codex().arun_single_turn(
                        user_id, prompt, access_token, model=model, timeout=timeout
                    )
                )
            else:
                text, usage = asyncio.run(
                    self._arun_single_turn(
                        user_id, prompt, access_token, model, timeout
                    )
                )
        except CliUsageLimitError as exc:
            self._persist_usage_limited(user_id, exc.resets_at)
            raise
        finally:
            self._finalize_codex_auth(user_id, access_token)
        self._record_usage(user_id, usage)
        return text

    def stream_single_turn(
        self,
        user_id: int,
        prompt: str,
        *,
        model: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> Iterator[str]:
        """Yield text chunks for one streaming turn (chat).

        The SDK's async generator is driven on a background thread that pushes
        chunks onto a queue; this sync generator relays them. Token resolution
        runs on the calling thread first, so all DB work stays synchronous.
        """
        access_token = self._resolve_access_token(user_id)
        chunk_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        # Populated by the driver thread with the turn's usage on a clean drain;
        # persisted below on this (sync) thread once the stream completes.
        usage_sink: list[_TurnUsage] = []

        def _drive() -> None:
            async def _run() -> None:
                try:
                    async for chunk in self._astream_for(
                        user_id, prompt, access_token, model, timeout, usage_sink
                    ):
                        chunk_queue.put(("chunk", chunk))
                except Exception as exc:  # noqa: BLE001 - relayed to the consumer
                    chunk_queue.put(("error", exc))
                finally:
                    chunk_queue.put(("done", None))

            asyncio.run(_run())

        thread = threading.Thread(target=_drive, name="cli-chat-stream", daemon=True)
        thread.start()
        try:
            while True:
                kind, payload = chunk_queue.get()
                if kind == "chunk":
                    yield payload  # type: ignore[misc]
                elif kind == "error":
                    if isinstance(payload, CliUsageLimitError):
                        self._persist_usage_limited(user_id, payload.resets_at)
                    raise payload  # type: ignore[misc]
                else:
                    break
        finally:
            thread.join(timeout=5)
            # Record the turn's usage on this (sync) thread, mirroring the
            # _persist_usage_limited call above. Empty on an errored turn.
            if usage_sink:
                self._record_usage(user_id, usage_sink[0])
            self._finalize_codex_auth(user_id, access_token)

    # ---- token lifecycle (sync DB; async only for the httpx refresh) ----

    def _resolve_access_token(self, user_id: int) -> str:
        with get_sync_session() as session:
            credential = session.exec(
                select(CliOAuthCredential).where(
                    CliOAuthCredential.user_id == user_id,
                    CliOAuthCredential.provider == self._provider,
                )
            ).first()
            if credential is None or (
                credential.status == CliOAuthCredentialStatus.REVOKED.value
            ):
                raise CliOAuthUnavailableError(
                    "No active CLI OAuth credential. Connect your subscription "
                    "in Settings > AI."
                )
            # Skip-when-limited: don't spawn a doomed subprocess while a known usage
            # limit is still in effect; raise now so the caller degrades to the
            # secondary or surfaces the reset time.
            self._raise_if_usage_limited(credential)
            if self._is_codex():
                # Codex stores the full auth.json blob; the CLI refreshes it in
                # place during inference (re-persisted afterwards — see
                # _finalize_codex_auth), so no httpx refresh here.
                blob = decrypt_secret(credential.access_token_encrypted)
                if not blob:
                    raise CliOAuthUnavailableError(
                        "ChatGPT credential is missing; reconnect in Settings > AI."
                    )
                return blob
            if not self._needs_refresh(credential):
                access_token = decrypt_secret(credential.access_token_encrypted)
                if not access_token:
                    raise CliOAuthUnavailableError(
                        "CLI OAuth credential is missing its access token; "
                        "reconnect in Settings > AI."
                    )
                return access_token
            return self._refresh_access_token(session, credential, user_id)

    def _refresh_access_token(
        self, session: Session, credential: CliOAuthCredential, user_id: int
    ) -> str:
        refresh_token = decrypt_secret(credential.refresh_token_encrypted)
        if not refresh_token:
            self._flip_status(
                session, credential, CliOAuthCredentialStatus.NEEDS_REAUTH
            )
            raise CliOAuthUnavailableError(
                "CLI OAuth access token expired and no refresh token is stored. "
                "Reconnect your subscription in Settings > AI."
            )
        try:
            # Both providers expose refresh_tokens and raise CliOAuthExchangeError
            # (codex_oauth reuses the class), so only the call target differs.
            refresh_module = codex_oauth if self._is_codex() else oauth
            tokens = asyncio.run(refresh_module.refresh_tokens(refresh_token))
        except oauth.CliOAuthExchangeError as exc:
            logger.warning("CLI OAuth refresh failed for user %s: %s", user_id, exc)
            self._flip_status(
                session, credential, CliOAuthCredentialStatus.NEEDS_REAUTH
            )
            raise CliOAuthUnavailableError(
                "CLI OAuth token refresh failed. Reconnect your subscription in "
                "Settings > AI."
            ) from exc
        credential.access_token_encrypted = encrypt_secret(tokens.access_token)
        if tokens.refresh_token is not None:  # rotating; keep the old one if absent
            credential.refresh_token_encrypted = encrypt_secret(tokens.refresh_token)
        credential.token_expires_at = (
            utc_now() + timedelta(seconds=tokens.expires_in)
            if tokens.expires_in
            else None
        )
        credential.status = CliOAuthCredentialStatus.ACTIVE.value
        credential.last_refreshed_at = utc_now()
        session.add(credential)
        session.commit()
        logger.info("Refreshed CLI OAuth token for user %s.", user_id)
        return tokens.access_token

    @staticmethod
    def _flip_status(
        session: Session,
        credential: CliOAuthCredential,
        status: CliOAuthCredentialStatus,
    ) -> None:
        credential.status = status.value
        session.add(credential)
        session.commit()

    @staticmethod
    def _needs_refresh(credential: CliOAuthCredential) -> bool:
        expires_at = credential.token_expires_at
        if expires_at is None:
            return True
        return utc_now() >= (expires_at - timedelta(seconds=_REFRESH_SKEW_SECONDS))

    @staticmethod
    def _raise_if_usage_limited(credential: CliOAuthCredential) -> None:
        limited_until = credential.usage_limited_until
        if limited_until is not None and limited_until > utc_now():
            raise CliUsageLimitError(
                _usage_limit_message(limited_until, None), resets_at=limited_until
            )

    def _persist_usage_limited(
        self, user_id: int, resets_at: Optional[datetime]
    ) -> None:
        # Only record a limit whose reset time we actually know (from a
        # RateLimitEvent), so skip-when-limited has a real horizon to check.
        if resets_at is None:
            return
        with get_sync_session() as session:
            credential = session.exec(
                select(CliOAuthCredential).where(
                    CliOAuthCredential.user_id == user_id,
                    CliOAuthCredential.provider == self._provider,
                )
            ).first()
            if credential is None:
                return
            credential.usage_limited_until = resets_at
            session.add(credential)
            session.commit()
            logger.warning(
                "CLI OAuth usage limit recorded for user %s until %s",
                user_id,
                resets_at,
            )

    def _record_usage(self, user_id: int, turn: _TurnUsage) -> None:
        """Persist a completed turn's token usage (daily rollup) and latest
        rate-limit reading. Best-effort: usage accounting must never break
        inference, so any failure is logged and swallowed."""
        if not turn.has_data:
            return
        try:
            with get_sync_session() as session:
                if turn.usage is not None:
                    self._increment_daily_usage(
                        session, user_id, turn.usage, turn.total_cost_usd
                    )
                if turn.reading is not None:
                    self._store_rate_limit_reading(session, user_id, turn.reading)
                session.commit()
        except Exception:  # noqa: BLE001 - accounting must not break inference
            logger.exception("Failed to record CLI usage for user %s", user_id)

    def _increment_daily_usage(
        self,
        session: Session,
        user_id: int,
        usage: dict[str, Any],
        cost: Optional[float],
    ) -> None:
        # Read-modify-write on today's (user, provider, date) rollup row. A rare
        # race between two concurrent turns can lose one increment; acceptable
        # for an advisory usage panel, and it matches _persist_usage_limited.
        today = utc_now().date()
        row = session.exec(
            select(CliUsageDaily).where(
                CliUsageDaily.user_id == user_id,
                CliUsageDaily.provider == self._provider,
                CliUsageDaily.usage_date == today,
            )
        ).first()
        if row is None:
            row = CliUsageDaily(
                user_id=user_id, provider=self._provider, usage_date=today
            )
        row.input_tokens += _as_int(usage.get("input_tokens"))
        row.output_tokens += _as_int(usage.get("output_tokens"))
        row.cache_read_input_tokens += _as_int(usage.get("cache_read_input_tokens"))
        row.cache_creation_input_tokens += _as_int(
            usage.get("cache_creation_input_tokens")
        )
        row.request_count += 1
        row.total_cost_usd += float(cost or 0.0)
        session.add(row)

    def _store_rate_limit_reading(
        self, session: Session, user_id: int, reading: _RateLimitReading
    ) -> None:
        credential = session.exec(
            select(CliOAuthCredential).where(
                CliOAuthCredential.user_id == user_id,
                CliOAuthCredential.provider == self._provider,
            )
        ).first()
        if credential is None:
            return
        credential.last_utilization = reading.utilization
        credential.last_rate_limit_status = reading.status
        credential.last_rate_limit_type = reading.rate_limit_type
        credential.last_rate_limit_at = utc_now()
        session.add(credential)

    # ---- async SDK drivers (wrapped by the sync entry points) ----

    def _build_options(
        self,
        access_token: str,
        model: Optional[str],
        user_id: int,
        *,
        include_partial_messages: bool = False,
    ):
        from claude_agent_sdk import ClaudeAgentOptions  # lazy: SDK only in worker-io

        return ClaudeAgentOptions(
            env=subscription_env_payload(access_token),
            cli_path=CLI_PATH,
            model=model,
            max_turns=1,
            allowed_tools=[],
            setting_sources=[],
            system_prompt=_SYSTEM_PROMPT,
            cwd=self._user_cwd(user_id),
            include_partial_messages=include_partial_messages,
        )

    async def _arun_single_turn(
        self,
        user_id: int,
        prompt: str,
        access_token: str,
        model: Optional[str],
        timeout: int,
    ) -> tuple[str, _TurnUsage]:
        from claude_agent_sdk import (  # lazy: SDK only in worker-io
            AssistantMessage,
            ClaudeSDKError,
            RateLimitEvent,
            ResultMessage,
            TextBlock,
            query,
        )

        options = self._build_options(access_token, model, user_id)
        parts: list[str] = []
        result_error: str | None = None
        rate_limit: tuple[Optional[datetime], Optional[str]] | None = None
        turn_usage = _TurnUsage()
        with scrubbed_environ():
            try:
                async with asyncio.timeout(timeout):
                    # Drain the generator fully (no early break) so the SDK closes
                    # its subprocess and async generator cleanly; ResultMessage is
                    # terminal for a single-turn query.
                    async for message in query(prompt=prompt, options=options):
                        if isinstance(message, AssistantMessage):
                            parts.extend(_assistant_text(message, TextBlock))
                        elif isinstance(message, RateLimitEvent):
                            rate_limit = _rate_limit_rejection(message) or rate_limit
                            turn_usage.reading = (
                                _rate_limit_reading(message) or turn_usage.reading
                            )
                        elif isinstance(message, ResultMessage):
                            result_error = _result_error(message)
                            _apply_result_usage(turn_usage, message)
            except (TimeoutError, ClaudeSDKError) as exc:
                raise _query_exception(exc, timeout) from exc
        _raise_if_terminal(rate_limit, result_error)
        text = "".join(parts).strip()
        if not text:
            raise CliOAuthUnavailableError("CLI inference returned no text.")
        return text, turn_usage

    async def _astream_single_turn(  # noqa: PLR0913 - cohesive SDK driver params
        self,
        user_id: int,
        prompt: str,
        access_token: str,
        model: Optional[str],
        timeout: int,
        usage_sink: list[_TurnUsage],
    ):
        from claude_agent_sdk import (  # lazy: SDK only in worker-io
            AssistantMessage,
            ClaudeSDKError,
            RateLimitEvent,
            ResultMessage,
            StreamEvent,
            TextBlock,
            query,
        )

        options = self._build_options(
            access_token, model, user_id, include_partial_messages=True
        )
        streamed_any = False
        result_error: str | None = None
        rate_limit: tuple[Optional[datetime], Optional[str]] | None = None
        turn_usage = _TurnUsage()
        with scrubbed_environ():
            try:
                async with asyncio.timeout(timeout):
                    async for message in query(prompt=prompt, options=options):
                        if isinstance(message, StreamEvent):
                            delta = _text_delta_from_stream_event(message)
                            if delta:
                                streamed_any = True
                                yield delta
                        elif isinstance(message, AssistantMessage):
                            # Fallback when partial-message deltas were unavailable:
                            # emit the assembled text once so chat still works.
                            if not streamed_any:
                                for block_text in _assistant_text(message, TextBlock):
                                    yield block_text
                        elif isinstance(message, RateLimitEvent):
                            rate_limit = _rate_limit_rejection(message) or rate_limit
                            turn_usage.reading = (
                                _rate_limit_reading(message) or turn_usage.reading
                            )
                        elif isinstance(message, ResultMessage):
                            result_error = _result_error(message)
                            _apply_result_usage(turn_usage, message)
            except (TimeoutError, ClaudeSDKError) as exc:
                raise _query_exception(exc, timeout) from exc
        _raise_if_terminal(rate_limit, result_error)
        # Reached only on a clean drain (no terminal error); hand the turn's
        # usage to the sync caller to persist.
        usage_sink.append(turn_usage)

    @staticmethod
    def _user_cwd(user_id: int) -> str:
        base = user_cli_dir(user_id)
        base.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(base, 0o700)
        except OSError:  # best-effort; exotic filesystems may reject chmod
            pass
        return str(base)


def _assistant_text(message, text_block_cls) -> list[str]:
    """Non-empty TextBlock texts from an AssistantMessage."""
    return [
        block.text
        for block in message.content
        if isinstance(block, text_block_cls) and block.text
    ]


def _result_error(message) -> str | None:
    """Error detail when a ResultMessage signals failure, else None."""
    if message.is_error:
        return f"CLI inference failed: {message.result or message.subtype}"
    return None


def _text_delta_from_stream_event(message) -> str:
    """Extract a text delta from a StreamEvent, or "" if it carries none.

    ``StreamEvent.event`` mirrors the Anthropic streaming event; text arrives as
    ``content_block_delta`` / ``text_delta``. Parsed defensively — the exact
    shape is verified by the auth smoke.
    """
    event = getattr(message, "event", None)
    if not isinstance(event, dict):
        return ""
    if event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta")
    if isinstance(delta, dict) and delta.get("type") == "text_delta":
        return delta.get("text") or ""
    return ""


def _rate_limit_rejection(message):
    """``(resets_at, rate_limit_type)`` if a RateLimitEvent is a rejection, else None.

    The SDK's ``RateLimitInfo.status`` is one of allowed/allowed_warning/rejected;
    only ``rejected`` means the call cannot proceed. ``resets_at`` is an epoch int
    (converted to naive UTC to match ``utc_now``); it may be absent.
    """
    info = getattr(message, "rate_limit_info", None)
    if info is None or getattr(info, "status", None) != "rejected":
        return None
    resets_at = getattr(info, "resets_at", None)
    dt = (
        datetime.fromtimestamp(resets_at, tz=timezone.utc).replace(tzinfo=None)
        if isinstance(resets_at, (int, float))
        else None
    )
    return dt, getattr(info, "rate_limit_type", None)


def _rate_limit_reading(message) -> Optional[_RateLimitReading]:
    """Latest rate-limit reading from a RateLimitEvent (any status), or None.

    Unlike ``_rate_limit_rejection`` this keeps non-rejection events too, so the
    ``utilization`` fraction from ``allowed``/``allowed_warning`` events — the
    only place a not-yet-exhausted reading arrives — is captured, not dropped.
    """
    info = getattr(message, "rate_limit_info", None)
    if info is None:
        return None
    status = getattr(info, "status", None)
    if status is None:
        return None
    utilization = getattr(info, "utilization", None)
    return _RateLimitReading(
        status=str(status),
        rate_limit_type=getattr(info, "rate_limit_type", None),
        utilization=(
            float(utilization) if isinstance(utilization, (int, float)) else None
        ),
    )


def _apply_result_usage(turn: _TurnUsage, message) -> None:
    """Copy token usage + notional cost from a ResultMessage into ``turn``.

    ``usage`` is the Anthropic-shaped dict (input/output/cache tokens);
    ``total_cost_usd`` is a notional API-equivalent figure for a flat-rate
    subscription (stored, never surfaced as real money)."""
    usage = getattr(message, "usage", None)
    if isinstance(usage, dict):
        turn.usage = usage
    cost = getattr(message, "total_cost_usd", None)
    if isinstance(cost, (int, float)):
        turn.total_cost_usd = float(cost)


def _as_int(value: Any) -> int:
    """Coerce a possibly-missing usage field to a non-negative int."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _usage_limit_message(
    resets_at: Optional[datetime], rate_limit_type: Optional[str]
) -> str:
    window = f" ({rate_limit_type} window)" if rate_limit_type else ""
    if resets_at is not None:
        return (
            f"Your subscription usage limit is reached{window}; it resets "
            f"around {resets_at:%H:%M UTC on %b %d}. Configure a fallback provider "
            f"in Settings > AI, or try again after that."
        )
    return (
        f"Your subscription usage limit is reached{window}. Configure a "
        f"fallback provider in Settings > AI, or try again later."
    )


def _usage_limit_error(
    resets_at: Optional[datetime], rate_limit_type: Optional[str]
) -> CliUsageLimitError:
    return CliUsageLimitError(
        _usage_limit_message(resets_at, rate_limit_type),
        resets_at=resets_at,
        rate_limit_type=rate_limit_type,
    )


def _classify_sdk_error(exc) -> CliOAuthUnavailableError:
    """Map a raised ClaudeSDKError to a usage-limit or generic unavailable error.

    A hard rate limit can surface as a non-zero CLI exit rather than a
    RateLimitEvent, so classify by the error text (no reset time available then).
    """
    msg = str(exc).lower()
    if any(
        token in msg
        for token in ("429", "rate limit", "rate_limit", "usage limit", "quota")
    ):
        return _usage_limit_error(None, None)
    return CliOAuthUnavailableError(f"CLI inference failed: {exc}")


def _query_exception(exc, timeout: int) -> CliOAuthUnavailableError:
    """Translate a terminal query exception (timeout or SDK error) to our type."""
    if isinstance(exc, TimeoutError):
        return CliOAuthUnavailableError(f"CLI inference timed out after {timeout}s.")
    return _classify_sdk_error(exc)


def _raise_if_terminal(
    rate_limit: tuple[Optional[datetime], Optional[str]] | None,
    result_error: Optional[str],
) -> None:
    """Raise the terminal error recorded while draining the query, if any."""
    if rate_limit is not None:
        raise _usage_limit_error(*rate_limit)
    if result_error:
        raise CliOAuthUnavailableError(result_error)
