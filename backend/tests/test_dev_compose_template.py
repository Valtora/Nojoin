"""The development compose template has to match the guide that ships it.

``docs/DEVELOPMENT.md`` appends a compose template and then, earlier in the same
document, tells a developer which services to rebuild against it. Nothing kept
the two in step: the rebuild loop named ``worker-gpu worker-cpu worker-io``
while the template defined a single ``worker``, so following the guide end to
end produced commands that error on a service that does not exist. The template
had also quietly fallen behind ``docker-compose.example.yml`` by five variables,
two of them read by ``config_manager`` -- a variable no service passes in is
read by nobody, the application default wins, and the developer gets no signal
that their setting was discarded.

``test_compose_env_plumbing.py`` guards the deployment template. This file
guards the development one, and pins it to its production counterpart so a lane
or a setting added to one cannot go missing from the other.

Parsed as text rather than with PyYAML for the reason given there: PyYAML
reaches this suite only as a transitive dependency of the model stack and is
declared in no requirements file.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE_PATH = REPO_ROOT / "docs" / "DEVELOPMENT.md"
COMPOSE_PATH = REPO_ROOT / "docker-compose.example.yml"

TEMPLATE_HEADING = "## Localhost Dev Compose Template"
SHARED_ANCHOR = "x-shared-app-environment"

# Services a rebuild command may name that the development template does not
# define. The document gives one command for docker-compose.example.yml, whose
# proxy service is `nginx` where the development template's is `nginx-dev`.
# Every entry is asserted to exist in that file, so the allowlist cannot absorb
# a typo.
EXAMPLE_ONLY_SERVICES = {"nginx"}

_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*):")
_SERVICE_RE = re.compile(r"^ {2}([a-z][a-z0-9-]*):\s*$")
_ENV_KEY_RE = re.compile(r"^(\s+)([A-Z][A-Z0-9_]*):")


def _yaml_blocks(markdown: str) -> list[str]:
    """Return the body of every ```yaml fence, in document order."""
    blocks: list[str] = []
    current: list[str] | None = None
    for line in markdown.splitlines():
        if current is None:
            if line.strip() == "```yaml":
                current = []
            continue
        if line.strip() == "```":
            blocks.append("\n".join(current))
            current = None
            continue
        current.append(line)
    return blocks


def _template() -> str:
    """Return the compose template appended to the development guide."""
    markdown = GUIDE_PATH.read_text(encoding="utf-8")
    _, _, after_heading = markdown.partition(TEMPLATE_HEADING)
    assert after_heading, f"{TEMPLATE_HEADING} is missing from {GUIDE_PATH.name}"
    blocks = _yaml_blocks(after_heading)
    assert blocks, "no yaml block follows the template heading"
    return blocks[0]


def _service_names(text: str) -> set[str]:
    """Collect the service keys under the top-level ``services:`` mapping."""
    names: set[str] = set()
    in_services = False
    for line in text.splitlines():
        if not in_services:
            in_services = line.startswith("services:")
            continue
        if line.strip() and _TOP_LEVEL_KEY_RE.match(line):
            break
        match = _SERVICE_RE.match(line)
        if match:
            names.add(match.group(1))
    return names


def _block_env_keys(text: str, opening: str, indent: int) -> set[str]:
    """Collect variable names declared under ``opening``.

    Reads from the opening line until the first line at or left of that line's
    own indentation, which is where the block ends in any valid YAML.
    """
    keys: set[str] = set()
    in_block = False
    for line in text.splitlines():
        if not in_block:
            if line.startswith(f"{' ' * indent}{opening}"):
                in_block = True
            continue
        stripped = line.strip()
        if stripped and len(line) - len(line.lstrip(" ")) <= indent:
            break
        match = _ENV_KEY_RE.match(line)
        if match:
            keys.add(match.group(2))
    return keys


def _shared_anchor_keys(text: str) -> set[str]:
    return _block_env_keys(text, f"{SHARED_ANCHOR}:", indent=0)


def _api_env_keys(text: str) -> set[str]:
    _, _, after_api = text.partition("\n  api:\n")
    assert after_api, "no api service found"
    return _block_env_keys(after_api, "environment:", indent=4)


def _rebuild_command_services(markdown: str) -> set[str]:
    """Collect every service named in a ``docker compose ... build`` command."""
    names: set[str] = set()
    for line in markdown.splitlines():
        tokens = line.strip().split()
        if tokens[:2] != ["docker", "compose"]:
            continue
        if "build" not in tokens and "--build" not in tokens:
            continue
        marker = "build" if "build" in tokens else "--build"
        for token in tokens[tokens.index(marker) + 1 :]:
            if not token.startswith("-"):
                names.add(token)
    return names


def _patch_snippet_services(markdown: str) -> set[str]:
    """Collect service keys from the guide's compose patch snippets.

    Everything except the appended template, which is the thing being patched.
    """
    names: set[str] = set()
    before_template, _, _ = markdown.partition(TEMPLATE_HEADING)
    for block in _yaml_blocks(before_template):
        names |= _service_names(block)
    return names


def test_template_is_parsed_at_all() -> None:
    """Guard the parser itself: a silent miss would pass every other test."""
    template = _template()
    assert "api" in _service_names(template)
    assert "DATABASE_URL" in _shared_anchor_keys(template)
    assert "FIRST_RUN_PASSWORD" in _api_env_keys(template)
    assert "api" in _rebuild_command_services(GUIDE_PATH.read_text(encoding="utf-8"))


def test_rebuild_commands_name_services_the_template_defines() -> None:
    markdown = GUIDE_PATH.read_text(encoding="utf-8")
    defined = _service_names(_template()) | EXAMPLE_ONLY_SERVICES
    missing = sorted(_rebuild_command_services(markdown) - defined)
    assert not missing, (
        f"{missing} are named in a rebuild command in {GUIDE_PATH.name} but the "
        "compose template it appends defines no such service, so the documented "
        "command fails for anyone following the guide."
    )


def test_patch_snippets_name_services_the_template_defines() -> None:
    markdown = GUIDE_PATH.read_text(encoding="utf-8")
    missing = sorted(_patch_snippet_services(markdown) - _service_names(_template()))
    assert not missing, (
        f"{missing} are patched by a snippet in {GUIDE_PATH.name} but the compose "
        "template it appends defines no such service, so the patch applies to "
        "nothing."
    )


def test_example_only_services_really_are_in_the_example_compose() -> None:
    """Keep the allowlist from absorbing a typo or a deleted service."""
    unknown = sorted(
        EXAMPLE_ONLY_SERVICES - _service_names(COMPOSE_PATH.read_text(encoding="utf-8"))
    )
    assert not unknown, f"{unknown} exist in neither compose file."


def test_worker_lanes_match_the_deployment_template() -> None:
    """A lane added to one template has to appear in the other.

    The point of the development template is that queue routing behaves locally
    as it does in production. A lane present in only one of the two files means
    some queue is drained live and never drained in development, or the reverse.
    """
    template_lanes = {
        name for name in _service_names(_template()) if name.startswith("worker")
    }
    deployment_lanes = {
        name
        for name in _service_names(COMPOSE_PATH.read_text(encoding="utf-8"))
        if name.startswith("worker")
    }
    assert template_lanes == deployment_lanes, (
        "the worker lanes in the docs/DEVELOPMENT.md template and in "
        "docker-compose.example.yml have diverged: "
        f"{sorted(template_lanes ^ deployment_lanes)}"
    )


def test_template_passes_every_shared_setting_the_deployment_template_does() -> None:
    compose_keys = _shared_anchor_keys(COMPOSE_PATH.read_text(encoding="utf-8"))
    missing = sorted(compose_keys - _shared_anchor_keys(_template()))
    assert not missing, (
        f"{missing} are passed to every service by docker-compose.example.yml but "
        f"not by the template in {GUIDE_PATH.name}. A development stack built from "
        "the guide silently discards them."
    )


def test_template_api_carries_every_setting_the_deployment_api_does() -> None:
    compose_keys = _api_env_keys(COMPOSE_PATH.read_text(encoding="utf-8"))
    missing = sorted(compose_keys - _api_env_keys(_template()))
    assert not missing, (
        f"{missing} reach the api service in docker-compose.example.yml but not in "
        f"the template in {GUIDE_PATH.name}, so the development API cannot be "
        "configured the way the deployment documentation describes."
    )
