"""Subprocess environment scrub for CLI OAuth inference.

Security-critical. The Claude Agent SDK builds the CLI subprocess environment by
merging the worker's ``os.environ`` *under* ``ClaudeAgentOptions.env``
(``claude_agent_sdk/_internal/transport/subprocess_cli.py`` builds
``{**inherited_env, "CLAUDE_CODE_ENTRYPOINT": ..., **options.env, ...}``).
Because that is a merge, ``options.env`` can only *override* keys, never *remove*
them — so an install-wide ``ANTHROPIC_API_KEY`` in the worker's environment would
survive into the subprocess and, per Claude Code's auth precedence, out-rank the
user's subscription token, silently billing the install key instead of the
user's plan.

The only reliable scrub is therefore to remove the key-auth variables from
``os.environ`` for the duration of the SDK call (so they are absent from
``inherited_env``) and inject the subscription token via ``options.env``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

# Variables that out-rank the subscription credential in Claude Code's auth
# precedence. Removing them from os.environ — not merely omitting them from
# options.env — is what actually keeps them out of the subprocess.
SCRUBBED_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

OAUTH_TOKEN_ENV_VAR = "CLAUDE_CODE_OAUTH_TOKEN"


@contextmanager
def scrubbed_environ() -> Iterator[None]:
    """Remove key-auth env vars from ``os.environ`` for the duration of the block.

    Prior values are restored on exit (and vars absent beforehand are left
    absent). Safe in a Celery prefork child, which runs a single task at a time,
    so nothing else reads ``os.environ`` while the SDK spawns the CLI subprocess.
    """
    saved: dict[str, str | None] = {
        var: os.environ.pop(var, None) for var in SCRUBBED_ENV_VARS
    }
    try:
        yield
    finally:
        for var, value in saved.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value


def subscription_env_payload(access_token: str) -> dict[str, str]:
    """The ``ClaudeAgentOptions.env`` payload: only the subscription OAuth token.

    Everything else the CLI needs (PATH, HOME, ...) comes from the inherited
    process environment; we deliberately add nothing else.
    """
    return {OAUTH_TOKEN_ENV_VAR: access_token}


# --- Codex (ChatGPT subscription) ---

# OpenAI key-auth vars that would make ``codex exec`` bill the API instead of the
# user's ChatGPT subscription. This is the Codex equivalent of the Claude scrub:
# CODEX_API_KEY / OPENAI_API_KEY select API billing, so they must never reach the
# subprocess. OPENAI_BASE_URL is dropped too so an install-wide proxy can't
# redirect the subscription call.
CODEX_SCRUBBED_ENV_VARS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "OPENAI_BASE_URL",
)


def codex_child_env(codex_home: str) -> dict[str, str]:
    """Build the Codex subprocess environment.

    The worker's environment minus the OpenAI key-auth vars, plus ``CODEX_HOME``
    (where the injected subscription ``auth.json`` lives). Unlike the Claude scrub
    — which must mutate ``os.environ`` because the Agent SDK merges it *under*
    ``options.env`` — the Codex driver spawns a raw subprocess and passes this
    dict as ``env`` explicitly, so excluding the keys here keeps them out of the
    child by construction (nothing is removed from ``os.environ`` in place).
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in CODEX_SCRUBBED_ENV_VARS
    }
    env["CODEX_HOME"] = codex_home
    return env
