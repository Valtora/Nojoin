"""The API must run without the packages only the worker images install.

The API image installs ``requirements/backend.txt`` and nothing else, while CI
installs the worker set as well, so a request that reaches SciPy, torch or any
other worker-only package passes every test and fails in production. The first
cut of live settling had two such paths. Pause and Finalize closed a settling
recording's lines through a check that imported the live label engine, and
with it SciPy, even though the answer was already stored. Finalize transcoded
pending browser segments through the live lane, whose settling step imported
the same engine at module level; the endpoint caught the error and counted
every pending segment as failed.

These tests block every installed package outside the API image's dependency
closure, worked out from ``requirements/backend.txt`` and the installed
metadata: ``test_worker_import_boundary.py`` the other way round.
"""

from __future__ import annotations

import functools
import importlib.metadata
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from sqlalchemy import Engine
from sqlmodel import Session

from backend.tests.canonical_engine import _recording, _transcript
from backend.tests.canonical_engine import (
    publication_engine as publication_engine,
)
from backend.tests.test_live_settling_publication import _request, _words
from backend.tests.test_worker_import_boundary import _imports
from backend.utils.canonical_pipeline.live_settling import publish_live_settling

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
REQUIREMENTS = REPO_ROOT / "requirements"

# What the API container imports from this repository: the app, its routes and
# services, and the MCP server it mounts.
API_ROOTS = (
    BACKEND_ROOT / "main.py",
    BACKEND_ROOT / "api",
    BACKEND_ROOT / "mcp_server",
)

_BLOCKER = """
import importlib.abc
import json
import sys

BLOCKED = frozenset(json.loads(sys.argv[1]))


class _WorkerOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


sys.meta_path.insert(0, _WorkerOnly())
"""

_IMPORT_ALL = """
import importlib
import traceback

import backend.main

failures = {}
for module, target, line in json.load(sys.stdin):
    try:
        importlib.import_module(target)
    except ModuleNotFoundError as error:
        # `from backend.x import name` is tried as a module too; only a
        # blocked package is a failure.
        if (error.name or "").partition(".")[0] in BLOCKED:
            failures[f"{module}:{line} imports {target}"] = traceback.format_exc(
                limit=-6
            )

print("REPORT " + json.dumps(failures))
"""

_CLOSE = """
from sqlalchemy import create_engine
from sqlmodel import Session

import backend.main
from backend.utils.canonical_pipeline import serialize_canonical_utterances
from backend.utils.canonical_pipeline.live_settling import close_live_settling

recording_id = int(sys.argv[3])
with Session(create_engine(sys.argv[2])) as session:
    closed = close_live_settling(session, recording_id=recording_id)
    session.commit()
    lines = serialize_canonical_utterances(session, recording_id)

print("REPORT " + json.dumps({"closed": closed, "locked": [line.get("locked") for line in lines]}))
"""


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(filename: str) -> list[Requirement]:
    pins: list[Requirement] = []
    for raw in (REQUIREMENTS / filename).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("-r "):
            pins.extend(_pins(line.removeprefix("-r ").strip()))
        elif line and not line.startswith("-"):
            pins.append(Requirement(line))
    return pins


def _api_distributions() -> set[str]:
    """Every distribution ``pip install -r requirements/backend.txt`` installs."""
    found: set[str] = set()
    pending = [(pin.name, frozenset(pin.extras)) for pin in _pins("backend.txt")]
    seen: set[tuple[str, frozenset[str]]] = set()
    while pending:
        name, extras = pending.pop()
        if (_normal(name), extras) in seen:
            continue
        seen.add((_normal(name), extras))
        found.add(_normal(name))
        try:
            requires = importlib.metadata.requires(name) or []
        except importlib.metadata.PackageNotFoundError:
            pytest.fail(f"Install requirements/backend.txt first: {name} is missing")
        for requirement in map(Requirement, requires):
            marker = requirement.marker
            if marker is None or any(
                marker.evaluate({"extra": extra}) for extra in extras or {""}
            ):
                pending.append((requirement.name, frozenset(requirement.extras)))
    return found


@functools.cache
def _worker_only_modules() -> tuple[str, ...]:
    """Top-level modules installed here that the API image does not have."""
    api = _api_distributions()
    return tuple(
        sorted(
            module
            for module, owners in importlib.metadata.packages_distributions().items()
            if not {_normal(owner) for owner in owners} & api
        )
    )


def _run(
    body: str, *args: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            _BLOCKER + textwrap.dedent(body),
            json.dumps(_worker_only_modules()),
            *args,
        ],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=600,
        cwd=REPO_ROOT,
    )


def _report(result: subprocess.CompletedProcess[str]) -> Any:
    reports = [
        line.removeprefix("REPORT ")
        for line in result.stdout.splitlines()
        if line.startswith("REPORT ")
    ]
    assert reports, (
        "The API-image run did not finish.\n"
        f"stdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-4000:]}"
    )
    return json.loads(reports[-1])


def _api_imports() -> list[tuple[str, str, int]]:
    """Every unguarded import in the API's own modules, at any depth."""
    paths = sorted(
        path
        for root in API_ROOTS
        for path in ([root] if root.is_file() else root.rglob("*.py"))
    )
    found: list[tuple[str, str, int]] = []
    for path in paths:
        parts = path.relative_to(REPO_ROOT).with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        found.extend(
            (module, name, line)
            for name, line, guarded in _imports(module, path)
            if not guarded
        )
    return found


def test_the_blocker_blocks_scipy_and_keeps_the_api_packages():
    """Guard the guard: SciPy is what closing lines reached; FastAPI is the API's own."""
    blocked = _worker_only_modules()
    assert "scipy" in blocked and "torch" in blocked
    assert "fastapi" not in blocked and "numpy" not in blocked
    result = _run("import scipy\nprint('IMPORTED')")
    assert "IMPORTED" not in result.stdout
    assert "No module named 'scipy'" in result.stderr


def test_api_imports_resolve_without_worker_packages():
    """Imports inside handlers count: Finalize's transcode import was one."""
    imports = _api_imports()
    assert any(
        target == "backend.processing.segment_transcode" for _, target, _ in imports
    )
    failures = _report(_run(_IMPORT_ALL, stdin=json.dumps(imports)))
    assert not failures, (
        "The API image lacks a package these imports reach. Keep the worker's "
        "ML stack behind the worker's own entry points:\n\n"
        + "\n".join(f"{name}:\n{trace}" for name, trace in failures.items())
    )


@pytest.mark.parametrize("publication_engine", ["sqlite"], indirect=True)
def test_closing_settling_lines_needs_no_worker_packages(
    publication_engine: Engine,
) -> None:
    """A GPU worker publishes lines that wait for labels; the API closes them."""
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = {
            "version": 1,
            "eligible": True,
            "generation": "api-boundary",
            "capture_epoch": 0,
            "sealed": False,
            "phase": "live",
            "policy": {"enabled": True},
            "speaker_enabled": True,
        }
        session.add(transcript)
        session.commit()
        assert publish_live_settling(
            session, request=_request(session, recording_id, 0), words=_words()
        ).accepted
        session.commit()

    url = publication_engine.url.render_as_string(hide_password=False)
    report = _report(_run(_CLOSE, url, str(recording_id)))

    assert report == {"closed": True, "locked": [True]}
