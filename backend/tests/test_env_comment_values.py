"""A comment Docker Compose passed on as a value is read as empty (#520).

Compose reads ``KEY= # note`` in ``.env`` as the value ``# note``. Fixing
``.env.example`` changes no operator's copy, so the backend package clears such
a value for the variables older templates wrote that way, once per process and
before anything reads them.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.backup.runtime import backup_export_directory
from backend.utils.config_manager import ConfigManager, get_system_api_keys
from backend.utils.env_comments import COMMENT_PRONE_ENV_VARS, clear_comment_values

REPO_ROOT = Path(__file__).resolve().parents[2]

# Values copied from the lines .env.example shipped before #520.
SHIPPED_COMMENTS = {
    "BACKUP_EXPORT_DIR": (
        "# Where exported backup archives are written (docs/BACKUP_RESTORE.md)."
    ),
    "HF_TOKEN": "# Optional: only needed to refresh bundled Pyannote assets",
    "NOJOIN_UMASK": "# umask for the application processes. Defaults to 0077",
    "SECONDARY_LLM_PROVIDER": "#One of: gemini, openai, anthropic, ollama",
}


def test_a_comment_is_emptied_and_named_in_one_warning(caplog) -> None:
    environ = {
        "HF_TOKEN": SHIPPED_COMMENTS["HF_TOKEN"],
        "NOJOIN_UMASK": SHIPPED_COMMENTS["NOJOIN_UMASK"],
        "OLLAMA_CONTEXT_WINDOW": "8192",
    }

    with caplog.at_level(logging.WARNING, logger="backend.utils.env_comments"):
        cleared = clear_comment_values(environ)

    assert cleared == ["NOJOIN_UMASK", "HF_TOKEN"]
    assert environ == {
        "HF_TOKEN": "",
        "NOJOIN_UMASK": "",
        "OLLAMA_CONTEXT_WINDOW": "8192",
    }
    [record] = caplog.records
    assert "NOJOIN_UMASK, HF_TOKEN" in record.getMessage()
    assert "OLLAMA_CONTEXT_WINDOW" not in record.getMessage()


def test_the_space_docker_run_keeps_is_ignored() -> None:
    """``docker run --env-file`` keeps the space after ``=`` that Compose trims."""
    environ = {"SECONDARY_LLM_PROVIDER": "  #One of: gemini"}

    assert clear_comment_values(environ) == ["SECONDARY_LLM_PROVIDER"]
    assert environ["SECONDARY_LLM_PROVIDER"] == ""


@pytest.mark.parametrize(
    "name", ["POSTGRES_PASSWORD", "REDIS_PASSWORD", "DATA_ENCRYPTION_KEY"]
)
def test_a_secret_that_starts_with_a_hash_is_left_alone(name: str, caplog) -> None:
    """A secret may legitimately start with ``#``, so the list is closed."""
    assert name not in COMMENT_PRONE_ENV_VARS
    environ = {name: "#a-real-secret"}

    with caplog.at_level(logging.WARNING, logger="backend.utils.env_comments"):
        assert clear_comment_values(environ) == []

    assert environ == {name: "#a-real-secret"}
    assert not caplog.records


def test_a_correct_env_logs_nothing(caplog) -> None:
    environ = {name: "" for name in COMMENT_PRONE_ENV_VARS}

    with caplog.at_level(logging.WARNING, logger="backend.utils.env_comments"):
        assert clear_comment_values(environ) == []

    assert not caplog.records


def test_the_readers_then_see_the_unset_value(monkeypatch, tmp_path) -> None:
    """The three costly cases from #520, read back through their own readers."""
    for name, comment in SHIPPED_COMMENTS.items():
        monkeypatch.setenv(name, comment)

    clear_comment_values()

    data_dir = SimpleNamespace(user_data_directory=str(tmp_path))
    assert backup_export_directory(data_dir) == tmp_path / "backups"
    assert "hf_token" not in get_system_api_keys()
    manager = ConfigManager(config_path=str(tmp_path / "config.json"))
    # Empty, as Compose passes an unset SECONDARY_LLM_PROVIDER: no fallback.
    assert manager.get("secondary_llm_provider") == ""


def test_importing_the_backend_package_clears_them_first() -> None:
    """Every API and worker process imports ``backend`` before it reads the
    environment, so that import is where the clearing has to happen. The umask
    proves the order: with the comment it fell back with an "Invalid" warning."""
    environ = {
        key: value
        for key, value in os.environ.items()
        if key not in COMMENT_PRONE_ENV_VARS
    }
    environ.update(SHIPPED_COMMENTS)
    environ["PYTHONPATH"] = str(REPO_ROOT)
    probe = (
        "import os, backend; "
        "print(repr(os.environ['NOJOIN_UMASK']), "
        "repr(os.environ['SECONDARY_LLM_PROVIDER']), oct(os.umask(0)))"
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=environ,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )

    assert result.stdout.split() == ["''", "''", "0o77"]
    assert "SECONDARY_LLM_PROVIDER" in result.stderr
    assert "Invalid NOJOIN_UMASK" not in result.stderr
