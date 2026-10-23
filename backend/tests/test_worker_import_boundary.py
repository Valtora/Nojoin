"""The workers must import without FastAPI or anything else only the API installs.

The worker images install ``requirements/worker.txt``, which has no FastAPI,
while CI installs the backend and worker sets together, so a worker module that
imports FastAPI passes every test there and fails in production. #660 was that:
the restore reached ``backend.utils.document_storage``, which imported FastAPI
at module level, and every backup, restore and backup cleanup on the workers
failed with ``No module named 'fastapi'``. The chain started at an import
inside the task functions, so the workers still started cleanly.

These tests work out every backend module a worker can reach, through imports
at module level and inside functions alike, then import them all in a
subprocess with the backend-only packages blocked, which is what the worker
images look like. ``test_api_torch_boundary.py`` holds the API to the same rule
the other way round.
"""

from __future__ import annotations

import ast
import importlib.metadata
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from backend.celery_app import celery_app

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
REQUIREMENTS = REPO_ROOT / "requirements"

# Top-level modules the API image has and the base worker image lacks: the pins
# only requirements/backend.txt makes, and what they pull in. worker-io carries
# a few of them through claude-agent-sdk, but worker-gpu and worker-cpu do not,
# and worker-cpu runs the backups.
BACKEND_ONLY_MODULES = frozenset(
    {
        "_re2",
        "annotated_doc",
        "docker",
        "fastapi",
        "httptools",
        "jsonschema",
        "jsonschema_specifications",
        "mcp",
        "mcp_types",
        "re2",
        "referencing",
        "reportlab",
        "rpds",
        "sse_starlette",
        "starlette",
        "uvicorn",
        "uvloop",
        "watchfiles",
    }
)

# Pinned in backend.txt and not in worker.txt, yet in the worker images anyway,
# as a dependency of something worker.txt installs.
IN_WORKER_IMAGES_ANYWAY = frozenset({"markdown-it-py"})  # through rich

# Installed in the worker images outside the requirements files, so absent from
# CI. A module that needs one is skipped by the import check; the static check
# below still reads it.
IMAGE_ONLY_PACKAGES = ("pyannote.audio",)

# What a worker container runs besides the Celery app's include list: the entry
# module named on the command line, the GPU preflight the entrypoint runs, the
# script diarisation starts in a child process, and the storage hygiene command
# an administrator runs in worker-io.
WORKER_ENTRY_MODULES = (
    "backend.celery_app",
    "backend.processing.gpu_preflight",
    "backend.processing.diarize_subprocess_entry",
    "backend.storage_hygiene",
)

_BLOCKER = """
import importlib.abc
import sys

BLOCKED = frozenset({blocked!r})


class _BackendOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
        return None


sys.meta_path.insert(0, _BackendOnly())
""".format(blocked=sorted(BACKEND_ONLY_MODULES))

_IMPORT_ALL = """
import importlib
import json
import traceback

image_only = {image_only!r}
modules = json.loads(sys.argv[1])
failures = {{}}
skipped = {{}}

# What `celery worker` imports as it starts: the app, then its include list.
from backend.celery_app import celery_app

celery_app.loader.import_default_modules()

for module in modules:
    try:
        importlib.import_module(module)
    except ModuleNotFoundError as error:
        missing = error.name or ""
        if missing.startswith(image_only) and missing.partition(".")[0] not in BLOCKED:
            skipped[module] = missing
        else:
            failures[module] = traceback.format_exc(limit=-6)
    except Exception:
        failures[module] = traceback.format_exc(limit=-6)

print("REPORT " + json.dumps({{"failures": failures, "skipped": skipped}}))
""".format(image_only=IMAGE_ONLY_PACKAGES)


def _module_path(module: str) -> Path | None:
    base = BACKEND_ROOT.parent.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _is_type_checking_block(node: ast.If) -> bool:
    test = node.test
    name = getattr(test, "id", None) or getattr(test, "attr", None)
    return name == "TYPE_CHECKING"


def _guards_import_error(node: ast.Try) -> bool:
    for handler in node.handlers:
        caught = handler.type
        names = (
            caught.elts if isinstance(caught, ast.Tuple) else [caught] if caught else []
        )
        for name in names:
            if getattr(name, "id", None) in {"ImportError", "ModuleNotFoundError"}:
                return True
    return False


def _import_targets(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:
        parts = package.split(".")
        base = parts[: len(parts) - node.level + 1]
        target = ".".join(base + ([node.module] if node.module else []))
    else:
        target = node.module or ""
    # `from backend.pkg import submodule` imports the submodule too.
    return [target] + [
        f"{target}.{alias.name}" for alias in node.names if alias.name != "*"
    ]


def _children(node: ast.AST, guarded: bool) -> list[tuple[ast.AST, bool]]:
    """The nodes under this one that can run, each with whether a guard covers it."""
    if isinstance(node, ast.If) and _is_type_checking_block(node):
        return [(child, guarded) for child in node.orelse]
    if isinstance(node, ast.Try) and _guards_import_error(node):
        rest = [*node.handlers, *node.orelse, *node.finalbody]
        return [(child, True) for child in node.body] + [
            (child, guarded) for child in rest
        ]
    return [(child, guarded) for child in ast.iter_child_nodes(node)]


def _imports(module: str, path: Path) -> list[tuple[str, int, bool]]:
    """Every module this file imports: (name, line, guarded by an ImportError handler).

    Imports inside functions count, since a task imports most of its work
    lazily. Imports under ``if TYPE_CHECKING:`` never run, so they do not.
    """
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: list[tuple[str, int, bool]] = []
    pending: list[tuple[ast.AST, bool]] = [
        (ast.parse(path.read_text(encoding="utf-8")), False)
    ]
    while pending:
        node, guarded = pending.pop()
        if isinstance(node, ast.Import | ast.ImportFrom):
            found.extend(
                (name, node.lineno, guarded) for name in _import_targets(node, package)
            )
        else:
            pending.extend(_children(node, guarded))
    return found


def _worker_roots() -> list[str]:
    roots = list(WORKER_ENTRY_MODULES) + list(celery_app.conf.include)
    for path in sorted((BACKEND_ROOT / "worker").rglob("*.py")):
        parts = path.relative_to(BACKEND_ROOT.parent).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        roots.append(".".join(parts))
    return roots


def _worker_reachable_modules() -> dict[str, Path]:
    """Every backend module a worker process can import, keyed by dotted name."""
    reachable: dict[str, Path] = {}
    pending = _worker_roots()
    while pending:
        module = pending.pop()
        if module in reachable:
            continue
        path = _module_path(module)
        if path is None:
            continue
        reachable[module] = path
        # Importing a.b.c runs a/__init__.py and a/b/__init__.py first.
        parent = module.rpartition(".")[0]
        if parent:
            pending.append(parent)
        pending.extend(
            name
            for name, _line, _guarded in _imports(module, path)
            if name.partition(".")[0] == "backend"
        )
    return reachable


def _run(body: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _BLOCKER + body, *args],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=REPO_ROOT,
    )


def test_the_blocker_blocks_fastapi():
    """Guard the guard: a blocker that lets FastAPI through proves nothing."""
    result = _run("import fastapi\nprint('IMPORTED')")
    assert "IMPORTED" not in result.stdout
    assert "No module named 'fastapi'" in result.stderr


def test_the_walk_follows_imports_inside_task_functions():
    """The #660 chain began at an import inside the backup and restore tasks."""
    reachable = _worker_reachable_modules()
    assert "backend.core.backup" in reachable
    assert "backend.utils.document_storage" in reachable
    assert not [name for name in reachable if name.startswith("backend.api.")]


def test_worker_modules_import_without_backend_only_packages():
    modules = sorted(_worker_reachable_modules())
    result = _run(_IMPORT_ALL, json.dumps(modules))
    reports = [
        line.removeprefix("REPORT ")
        for line in result.stdout.splitlines()
        if line.startswith("REPORT ")
    ]
    assert reports, (
        "The import check did not finish.\n"
        f"stdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-4000:]}"
    )
    report = json.loads(reports[-1])
    assert not report["failures"], (
        "These modules fail to import in a worker image, which has no FastAPI "
        "or other package only requirements/backend.txt installs. Raise a "
        "domain error and convert it in the endpoint instead:\n\n"
        + "\n".join(
            f"{module}:\n{trace}" for module, trace in report["failures"].items()
        )
    )


def test_worker_modules_never_import_backend_only_packages():
    """Catch an import of one inside a function, which no import check runs.

    An import guarded by an ``except ImportError`` or ``ModuleNotFoundError``
    is allowed: the calendar service keeps its API-facing helpers that way.
    """
    offenders = sorted(
        {
            f"{path.relative_to(REPO_ROOT)}:{line} imports {name.partition('.')[0]}"
            for module, path in _worker_reachable_modules().items()
            for name, line, guarded in _imports(module, path)
            if name.partition(".")[0] in BACKEND_ONLY_MODULES and not guarded
        }
    )
    assert not offenders, (
        "Modules the workers import must not import a package only the API "
        "image installs:\n" + "\n".join(offenders)
    )


def _requirement_names(filename: str) -> set[str]:
    names = set()
    for raw in (REQUIREMENTS / filename).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[\[<>=!~;@\s]", line, maxsplit=1)[0]
        names.add(re.sub(r"[-_.]+", "-", name).lower())
    return names


def test_every_backend_only_requirement_is_blocked():
    """A new pin in backend.txt alone must join the blocked modules above."""
    modules_of: dict[str, set[str]] = {}
    for module, distributions in importlib.metadata.packages_distributions().items():
        for distribution in distributions:
            key = re.sub(r"[-_.]+", "-", distribution).lower()
            modules_of.setdefault(key, set()).add(module)

    backend_only = (
        _requirement_names("backend.txt")
        - _requirement_names("worker.txt")
        - IN_WORKER_IMAGES_ANYWAY
    )
    not_installed = sorted(name for name in backend_only if name not in modules_of)
    if not_installed:
        pytest.fail(f"Install requirements/backend.txt first: {not_installed}")
    unblocked = {
        name: sorted(modules_of[name] - BACKEND_ONLY_MODULES)
        for name in sorted(backend_only)
        if modules_of[name] - BACKEND_ONLY_MODULES
    }
    assert not unblocked, (
        "Add these to BACKEND_ONLY_MODULES, or to IN_WORKER_IMAGES_ANYWAY if "
        f"the worker images install them through another package: {unblocked}"
    )
