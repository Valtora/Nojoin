"""Fetch the Codex model catalogue from the installed codex binary.

``codex debug models`` renders the embedded, version-accurate model catalogue as
JSON — no auth, no network — so the model picker reflects exactly what the
deployed codex build supports rather than a hard-coded guess (which drifts every
codex release). worker-io only (the binary lives there); the result is cached in
Redis and served to the browser by the API.
"""

from __future__ import annotations

import json
import logging
import subprocess

from backend.processing.cli.codex_login import CODEX_PATH
from backend.processing.cli.env_scrub import codex_child_env
from backend.services.cli_oauth.persistence import user_cli_dir

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20


def fetch_model_catalog() -> list[dict]:
    """Return ``[{"id": slug, "label": display_name}]`` for the user-facing
    models (``visibility == "list"``), ordered by codex's own priority. Empty on
    any failure (the API then serves a curated fallback)."""
    # A shared scratch CODEX_HOME (no per-user credential needed — the catalogue
    # is embedded in the binary). user_cli_dir(0) is a non-user scratch namespace.
    codex_home = user_cli_dir(0) / "codex-catalog"
    codex_home.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [CODEX_PATH, "debug", "models"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=codex_child_env(str(codex_home)),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("codex debug models failed: %s", exc)
        return []
    if result.returncode != 0:
        logger.warning(
            "codex debug models exit %s: %s", result.returncode, result.stderr[:200]
        )
        return []
    return _parse_catalog(result.stdout)


def _parse_catalog(raw: str) -> list[dict]:
    try:
        # codex may emit a leading warning line before the JSON; start at the {.
        catalog = json.loads(raw[raw.index("{") :])
    except (ValueError, KeyError):
        logger.warning("codex debug models returned unparseable output")
        return []
    listed = [
        model
        for model in catalog.get("models", [])
        if model.get("visibility") == "list" and model.get("slug")
    ]
    listed.sort(key=lambda model: model.get("priority", 1_000_000))
    return [
        {"id": model["slug"], "label": model.get("display_name") or model["slug"]}
        for model in listed
    ]
