"""Read a ``.env`` comment that Docker Compose passed on as a value as empty.

Compose strips an inline comment from an unquoted ``.env`` value only where
`` #`` follows the value, so ``KEY= # note`` reaches every container that
declares ``KEY`` as the value ``# note``. Released ``.env.example`` files wrote
nine variables that way (#520), and fixing the template changes no operator's
copy. No valid value of any of them starts with ``#``, so a value that does is
read as the empty value the template meant, and one warning per process names
each variable so the operator can fix the line.

``backend/__init__.py`` runs this before it sets the umask and before any other
module reads the environment. It rewrites ``os.environ`` itself, so every
reader and every child process sees the corrected value.

The list is closed on purpose. A password or an encryption key may start with
``#``, so a rule applied to every variable could blank a real secret.
"""

from __future__ import annotations

import logging
import os
from collections.abc import MutableMapping

logger = logging.getLogger(__name__)

# Every variable a released .env.example offered as ``KEY= # comment``.
# test_compose_env_plumbing.py fails on a new line of that shape, so the list
# only ever has to cover copies of older templates.
COMMENT_PRONE_ENV_VARS: tuple[str, ...] = (
    "NOJOIN_TELEMETRY_ENABLED",
    "BACKUP_EXPORT_DIR",
    "NOJOIN_UMASK",
    "NOJOIN_STALL_WATCHDOG_ENABLED",
    "HF_TOKEN",
    "LLM_PROVIDER",
    "OLLAMA_CONTEXT_WINDOW",
    "SECONDARY_LLM_PROVIDER",
    "SECONDARY_OLLAMA_CONTEXT_WINDOW",
)


def clear_comment_values(
    environ: MutableMapping[str, str] | None = None,
) -> list[str]:
    """Empty each listed variable whose value is a comment, and name them.

    Leading whitespace is ignored because ``docker run --env-file`` keeps the
    space after the equals sign that Compose trims. Returns the variables it
    emptied, in the order of ``COMMENT_PRONE_ENV_VARS``.
    """
    env: MutableMapping[str, str] = os.environ if environ is None else environ
    cleared = [
        name
        for name in COMMENT_PRONE_ENV_VARS
        if env.get(name, "").lstrip().startswith("#")
    ]
    for name in cleared:
        env[name] = ""
    if cleared:
        logger.warning(
            "Reading %s as empty: the value is a comment, which Docker Compose "
            "passes on when a .env line has nothing before the #. Move the "
            "comment to its own line or delete it (docs/DEPLOYMENT.md#configure-env).",
            ", ".join(cleared),
        )
    return cleared
