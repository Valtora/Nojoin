"""Packages installed with --no-deps still get what they declare (#526).

requirements/worker-no-deps.txt holds the packages every install surface puts
in with ``pip install --no-deps`` after worker.txt. fastembed is the only one:
it caps pillow below 13.0, the first release with the fixes for 20 advisories,
and Nojoin loads none of the image models that cap serves. pip no longer checks
what these packages declare, so this module does. Every declared requirement
must be pinned in worker.txt or a file it includes, at a version the package
accepts, unless OVERRIDES names it. An override must still be needed: once the
package accepts the pinned version, it belongs back in worker.txt.
"""

from __future__ import annotations

import importlib.metadata as metadata
import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements"
NO_DEPS_FILE = REQUIREMENTS / "worker-no-deps.txt"

# Declared requirements Nojoin overrides on purpose, by package.
OVERRIDES: dict[str, set[str]] = {
    # pillow<13.0 blocks the release that fixes 20 advisories (#526).
    "fastembed": {"pillow"},
}

# Pinned only in worker.txt, so an install that has it installed worker.txt.
WORKER_SENTINEL = "onnx-asr"

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;#]+)")
_INCLUDE = re.compile(r"^-r\s+(\S+)")


def _pins(path: Path) -> dict[str, str]:
    """Exact pins in ``path`` and in every file it includes with ``-r``."""
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        include = _INCLUDE.match(line)
        if include:
            pins.update(_pins(path.parent / include.group(1)))
        elif pin := _PIN.match(line):
            pins[canonicalize_name(pin.group(1))] = pin.group(2)
    return pins


def _entries(path: Path) -> list[str]:
    lines = (raw.strip() for raw in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _installed(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def _declared(package: str) -> list[Requirement]:
    """What ``package`` requires on this interpreter, extras left out."""
    requirements = [Requirement(raw) for raw in metadata.requires(package) or []]
    return [
        requirement
        for requirement in requirements
        if requirement.marker is None or requirement.marker.evaluate({"extra": ""})
    ]


def _problems(package: str, pins: dict[str, str]) -> list[str]:
    declared = _declared(package)
    overrides = OVERRIDES.get(package, set())
    problems = [
        f"OVERRIDES names {name} for {package}, which no longer declares it."
        for name in sorted(overrides - {canonicalize_name(r.name) for r in declared})
    ]
    for requirement in declared:
        name = canonicalize_name(requirement.name)
        pinned = pins.get(name)
        accepted = pinned is not None and requirement.specifier.contains(
            pinned, prereleases=True
        )
        if name in overrides:
            if accepted:
                problems.append(
                    f"{package} now accepts {name}=={pinned}, so it no longer needs "
                    f"--no-deps. Move it back into worker.txt, delete "
                    f"worker-no-deps.txt and its install lines, and drop it here."
                )
        elif pinned is None:
            problems.append(
                f"{package} requires {requirement}, which worker.txt does not pin."
            )
        elif not accepted:
            problems.append(
                f"{package} requires {requirement}, but worker.txt pins "
                f"{name}=={pinned}."
            )
    return problems


def test_no_deps_file_holds_only_exact_pins() -> None:
    entries = _entries(NO_DEPS_FILE)
    assert entries
    for entry in entries:
        assert _PIN.match(entry), f"{entry!r} is not an exact pin"
    assert set(OVERRIDES) <= set(_pins(NO_DEPS_FILE))


def test_no_other_requirements_file_resolves_a_no_deps_package() -> None:
    no_deps = set(_pins(NO_DEPS_FILE))
    for path in sorted(REQUIREMENTS.glob("*.txt")):
        if path == NO_DEPS_FILE:
            continue
        clash = no_deps & set(_pins(path))
        assert not clash, (
            f"{path.name} resolves {sorted(clash)} with its dependencies, which "
            f"worker-no-deps.txt exists to skip."
        )


@pytest.mark.parametrize("package", sorted(_pins(NO_DEPS_FILE)))
def test_worker_pins_what_each_no_deps_package_requires(package: str) -> None:
    installed = _installed(package)
    if installed is None:
        if _installed(WORKER_SENTINEL) is not None:
            pytest.fail(
                f"worker.txt is installed but {package} is not. Run: python -m pip "
                f"install --no-deps -r requirements/worker-no-deps.txt"
            )
        pytest.skip("the worker requirements are not installed")

    pinned = _pins(NO_DEPS_FILE)[package]
    assert installed == pinned, (
        f"{package} {installed} is installed but worker-no-deps.txt pins {pinned}; "
        f"reinstall it to check the pinned release's requirements."
    )
    problems = _problems(package, _pins(REQUIREMENTS / "worker.txt"))
    assert not problems, "\n".join(problems)
