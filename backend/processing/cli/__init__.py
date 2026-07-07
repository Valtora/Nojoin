"""CLI OAuth inference driver (worker-io only).

Drives the Claude Code CLI as locked-down single-turn subprocesses via the
Claude Agent SDK, authenticated by a user's subscription OAuth token. Imported
only inside the ``worker-io`` image; ``claude_agent_sdk`` is imported lazily
inside the call methods so the API and gpu/cpu worker images never load it.
"""

from backend.processing.cli.manager import (
    CliConversationManager,
    CliOAuthUnavailableError,
)

__all__ = ["CliConversationManager", "CliOAuthUnavailableError"]
