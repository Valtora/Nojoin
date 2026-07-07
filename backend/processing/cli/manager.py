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
from datetime import timedelta
from pathlib import Path
from typing import Iterator, Optional

from sqlmodel import Session, select

from backend.core.db import get_sync_session
from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.cli_oauth import (
    CliOAuthCredential,
    CliOAuthCredentialStatus,
    CliOAuthProvider,
)
from backend.processing.cli.env_scrub import scrubbed_environ, subscription_env_payload
from backend.services.cli_oauth import oauth
from backend.utils.path_manager import path_manager
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


class CliOAuthUnavailableError(RuntimeError):
    """CLI OAuth inference could not proceed (no active credential, a failed
    refresh, or an SDK/CLI error). ``SecondaryLLMBackend`` degrades to the user's
    configured fallback when one exists; otherwise it surfaces to the caller."""


class CliConversationManager:
    """Drives single-turn Claude Agent SDK queries for one subscription provider.

    Stateless across calls (a fresh session per call) for M3b async tasks and
    chat; the resumable-session lane for live Meeting Edge lands in M4.
    """

    def __init__(self, provider: str = CliOAuthProvider.CLAUDE_CODE.value) -> None:
        self._provider = provider

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
        return asyncio.run(
            self._arun_single_turn(user_id, prompt, access_token, model, timeout)
        )

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

        def _drive() -> None:
            async def _run() -> None:
                try:
                    async for chunk in self._astream_single_turn(
                        user_id, prompt, access_token, model, timeout
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
                    raise payload  # type: ignore[misc]
                else:
                    break
        finally:
            thread.join(timeout=5)

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
                    "No active CLI OAuth credential. Connect your Claude "
                    "subscription in Settings > AI."
                )
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
            tokens = asyncio.run(oauth.refresh_tokens(refresh_token))
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
    ) -> str:
        from claude_agent_sdk import (  # lazy: SDK only in worker-io
            AssistantMessage,
            ResultMessage,
            TextBlock,
            query,
        )

        options = self._build_options(access_token, model, user_id)
        parts: list[str] = []
        result_error: str | None = None
        with scrubbed_environ():
            try:
                async with asyncio.timeout(timeout):
                    # Drain the generator fully (no early break) so the SDK closes
                    # its subprocess and async generator cleanly; ResultMessage is
                    # terminal for a single-turn query.
                    async for message in query(prompt=prompt, options=options):
                        if isinstance(message, AssistantMessage):
                            parts.extend(_assistant_text(message, TextBlock))
                        elif isinstance(message, ResultMessage):
                            result_error = _result_error(message)
            except TimeoutError as exc:
                raise CliOAuthUnavailableError(
                    f"CLI inference timed out after {timeout}s."
                ) from exc
        if result_error:
            raise CliOAuthUnavailableError(result_error)
        text = "".join(parts).strip()
        if not text:
            raise CliOAuthUnavailableError("CLI inference returned no text.")
        return text

    async def _astream_single_turn(
        self,
        user_id: int,
        prompt: str,
        access_token: str,
        model: Optional[str],
        timeout: int,
    ):
        from claude_agent_sdk import (  # lazy: SDK only in worker-io
            AssistantMessage,
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
                        elif isinstance(message, ResultMessage):
                            result_error = _result_error(message)
            except TimeoutError as exc:
                raise CliOAuthUnavailableError(
                    f"CLI inference timed out after {timeout}s."
                ) from exc
        if result_error:
            raise CliOAuthUnavailableError(result_error)

    @staticmethod
    def _user_cwd(user_id: int) -> str:
        base = Path(path_manager.user_data_directory) / "cli-oauth" / str(user_id)
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
