"""The deployment templates have to carry every setting they promise.

An operator configures Nojoin through three lists that nothing keeps in step:
``docs/DEPLOYMENT.md`` describes a variable, ``.env.example`` prompts for it,
and ``docker-compose.example.yml`` is the only one of the three that actually
puts it inside a container. Compose has no pass-everything-through mode, so a
variable missing from the last list is read by nobody and fails silently: the
stack starts, the application default wins, and the operator has no signal that
their setting was discarded. That is how ``OLLAMA_CONTEXT_WINDOW`` shipped
documented but inert.

Parsed as text rather than with PyYAML deliberately: PyYAML reaches this suite
only as a transitive dependency of the model stack and is declared in no
requirements file, so importing it here would add an undeclared dependency to
guard against a formatting change that has never happened.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.utils.config_manager import ENV_OVERRIDES
from backend.utils.stall_watchdog import (
    ENABLED_ENV,
    INTERVAL_ENV,
    LAG_THRESHOLD_ENV,
    PRESSURE_THRESHOLD_ENV,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.example.yml"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
# The source-build stack in DEVELOPMENT.md, which copies the shared anchor.
DEVELOPMENT_DOC_PATH = REPO_ROOT / "docs" / "DEVELOPMENT.md"

# The anchor every service running application code merges in. It is the only
# placement that guarantees the API and all four worker lanes agree, which these
# settings need: config_manager is loaded in both, so a value present on one
# side only makes a worker disagree with the API about the active provider.
SHARED_ANCHOR = "x-shared-app-environment"

# Read by the API process alone (backend/main.py starts the watchdog), so the
# api service is where they belong rather than the shared anchor.
STALL_WATCHDOG_ENV = (
    ENABLED_ENV,
    INTERVAL_ENV,
    LAG_THRESHOLD_ENV,
    PRESSURE_THRESHOLD_ENV,
)

_ENV_KEY_RE = re.compile(r"^(\s+)([A-Z][A-Z0-9_]*):")
_BLANK_DEFAULT_RE = re.compile(r"^\s+([A-Z][A-Z0-9_]*):\s*\$\{\1:-\}\s*$", re.MULTILINE)
_INTERPOLATION_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")


def _block_env_keys(text: str, opening: str) -> set[str]:
    """Collect the variable names declared under the first ``opening`` line.

    Reads from that line until the first line at or left of its own
    indentation, which is where the block ends in any valid YAML.
    """
    indent = len(opening) - len(opening.lstrip(" "))
    keys: set[str] = set()
    in_block = False
    for line in text.splitlines():
        if not in_block:
            if line.startswith(opening):
                in_block = True
            continue
        if line.strip() and len(line) - len(line.lstrip(" ")) <= indent:
            break
        match = _ENV_KEY_RE.match(line)
        if match:
            keys.add(match.group(2))
    return keys


def _shared_anchor_env_keys(text: str) -> set[str]:
    """Collect the variable names declared in the shared environment anchor."""
    return _block_env_keys(text, f"{SHARED_ANCHOR}:")


def _api_env_keys(text: str) -> set[str]:
    """Collect the variable names the api service declares itself."""
    _, _, after_api = text.partition("\n  api:\n")
    assert after_api, "no api service found"
    return _block_env_keys(after_api, "    environment:")


def _blank_default_keys(text: str) -> set[str]:
    """Variables the compose file declares with no fallback of its own.

    ``FOO: ${FOO:-}`` is the template's way of saying "the code owns this
    default". Compose still sets the variable, so the process sees an empty
    string rather than nothing, anywhere in the file — not only in the shared
    anchor, since the worker lanes carry their own.
    """
    return set(_BLANK_DEFAULT_RE.findall(text))


def _code_default_reads(key: str) -> list[str]:
    """Files reading ``key`` with a non-empty fallback passed to get()/getenv().

    That form is only reached when the variable is absent, which under compose
    it never is, so the fallback is dead and the empty string wins.
    """
    pattern = re.compile(
        r"os\.(?:environ\.get|getenv)\(\s*[\"']"
        + re.escape(key)
        + r"[\"']\s*,\s*[\"'][^\"']+[\"']"
    )
    return sorted(
        str(path.relative_to(REPO_ROOT))
        for path in sorted((REPO_ROOT / "backend").rglob("*.py"))
        if pattern.search(path.read_text(encoding="utf-8"))
    )


def _env_example_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0].strip())
    return keys


def _compose_dotenv_values(text: str) -> dict[str, str]:
    """Read unquoted ``.env`` values the way Docker Compose does.

    Compose trims the space after the equals sign before it looks for an
    inline comment, and it strips a comment only where `` #`` follows a value.
    ``KEY= # note`` therefore reaches the containers as ``# note``.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        values[key.strip()] = raw.lstrip().split(" #", 1)[0].rstrip()
    return values


def test_shared_anchor_is_parsed_at_all() -> None:
    """Guard the parser itself: a silent miss would pass every other test."""
    keys = _shared_anchor_env_keys(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert "DATABASE_URL" in keys
    assert "LLM_PROVIDER" in keys


def test_env_overrides_reach_the_containers() -> None:
    keys = _shared_anchor_env_keys(COMPOSE_PATH.read_text(encoding="utf-8"))
    missing = sorted(set(ENV_OVERRIDES.values()) - keys)
    assert not missing, (
        f"{missing} are read by config_manager but no service passes them in. "
        f"Add them to the {SHARED_ANCHOR} anchor in docker-compose.example.yml, "
        "or an operator who sets them gets the application default and no warning."
    )


def test_api_environment_is_parsed_at_all() -> None:
    """Guard the api parser, including where it stops."""
    keys = _api_env_keys(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert "FIRST_RUN_PASSWORD" in keys
    assert "NOJOIN_TRUSTED_PROXIES" in keys
    # worker-gpu's own variable: seeing it means the block ran past the api.
    assert "NVIDIA_VISIBLE_DEVICES" not in keys


def test_stall_watchdog_settings_reach_the_api() -> None:
    """DEPLOYMENT.md documents all four, and the api never received them."""
    keys = _api_env_keys(COMPOSE_PATH.read_text(encoding="utf-8"))
    missing = [name for name in STALL_WATCHDOG_ENV if name not in keys]
    assert not missing, (
        f"{missing} are read by backend/utils/stall_watchdog.py but the api "
        "service in docker-compose.example.yml does not pass them in, so a "
        "value set in .env has no effect."
    )


def test_every_env_example_setting_reaches_compose() -> None:
    """A line in .env.example that compose never interpolates is read by nobody.

    Compose reads .env only to fill ``${VAR}`` references, so a key the file
    never references is dropped before any container starts. That is how
    ``NOJOIN_STALL_WATCHDOG_ENABLED`` was offered to operators and did nothing.
    """
    offered = _env_example_keys(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"))
    referenced = set(
        _INTERPOLATION_RE.findall(COMPOSE_PATH.read_text(encoding="utf-8"))
    )
    missing = sorted(offered - referenced)
    assert not missing, (
        f"{missing} are offered in .env.example but docker-compose.example.yml "
        "never references them, so setting them changes nothing."
    )


@pytest.mark.parametrize(
    "template", [COMPOSE_PATH, DEVELOPMENT_DOC_PATH], ids=lambda path: path.name
)
def test_llm_provider_reaches_the_containers_empty(template: Path) -> None:
    """The setup wizard reads LLM_PROVIDER itself to tell an operator who chose
    gemini from one who chose nothing. A compose default of gemini made every
    install look like the first, so with no key the wizard said the server's
    .env selected Gemini instead of saying no provider was configured (#477).
    config_manager supplies gemini for the empty value."""
    declarations = re.findall(
        r"^\s+LLM_PROVIDER:\s*(.+?)\s*$",
        template.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert declarations, f"{template.name} does not declare LLM_PROVIDER"
    assert set(declarations) == {"${LLM_PROVIDER:-}"}


def test_compose_dotenv_reader_keeps_a_comment_after_an_empty_value() -> None:
    """Guard the reader. Docker Compose 2.40 renders these five lines the
    same way: the comment is the value when nothing precedes it."""
    assert _compose_dotenv_values(
        "A= # note\nB=x # note\nC=\nD=#note\nE=  # note\n"
    ) == {
        "A": "# note",
        "B": "x",
        "C": "",
        "D": "#note",
        "E": "# note",
    }


def test_env_example_leaves_llm_provider_empty() -> None:
    """.env.example is what an operator copies, so its LLM_PROVIDER line is
    what setting no provider means in practice. Compose read the inline comment
    that used to follow it as the value, so the wizard reported that .env
    selected "#One of: gemini, openai, anthropic, ollama"."""
    values = _compose_dotenv_values(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert values["LLM_PROVIDER"] == ""


def test_compose_reads_no_env_example_value_as_a_comment() -> None:
    """.env.example is what an operator copies, so a line Compose misreads
    there is misread on every install made from it. Eight lines kept the
    ``KEY= # note`` shape after #521 fixed LLM_PROVIDER: BACKUP_EXPORT_DIR named a
    directory inside each container, HF_TOKEN reported a token as set, and
    SECONDARY_LLM_PROVIDER named a provider that does not exist (#520)."""
    values = _compose_dotenv_values(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert values, "the .env.example reader found no values"
    commented = sorted(key for key, value in values.items() if value.startswith("#"))
    assert not commented, (
        f"Compose reads the inline comment on {commented} in .env.example as "
        "the value. Move each comment to its own line above the variable."
    )


def test_blank_defaults_are_parsed_at_all() -> None:
    """Guard the parser: an empty set would make the test below vacuous."""
    keys = _blank_default_keys(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert "NOJOIN_CODEX_PATH" in keys
    assert "OLLAMA_CONTEXT_WINDOW" in keys


def test_blank_compose_defaults_do_not_shadow_code_defaults() -> None:
    """The other half of the plumbing problem, and the more expensive half.

    Passing a variable through fixed the silent-discard failure above but
    created its mirror image: ``${FOO:-}`` puts an empty string in the
    environment, so ``os.environ.get("FOO", "a-default")`` stops returning its
    default the moment the variable is plumbed through. That is how
    ``NOJOIN_CODEX_PATH`` turned into an empty binary path and took Codex note
    generation down with it. Read these with ``or`` instead.
    """
    offenders = {
        key: files
        for key in sorted(_blank_default_keys(COMPOSE_PATH.read_text(encoding="utf-8")))
        if (files := _code_default_reads(key))
    }
    assert not offenders, (
        f"{offenders} are declared empty in docker-compose.example.yml but read "
        "with a get() fallback, which compose makes unreachable. Use "
        'os.environ.get("VAR", "").strip() or "<default>" instead.'
    )


def test_env_overrides_are_offered_to_operators() -> None:
    keys = _env_example_keys(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"))
    missing = sorted(set(ENV_OVERRIDES.values()) - keys)
    assert not missing, (
        f"{missing} are supported but absent from .env.example, so nothing tells "
        "an operator the setting exists."
    )
