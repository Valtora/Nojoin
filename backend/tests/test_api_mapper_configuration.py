"""The ORM mapper graph must configure from every real entry point.

Relationships name their targets by string, so SQLAlchemy resolves them only
once every class exists. Most test modules import ``backend.models.registry``,
which registers everything and therefore hides a missing registration -- the
whole suite can pass while production fails at start with:

    expression 'DocumentPage.page_number' failed to locate a name

That is not hypothetical: it is what shipped, twice, once for ``DocumentPage``
and once for ``ContextChunk``.

``backend.core.db`` is the chokepoint that fixes it. Every entry point imports
it (nothing touches the database without it) and no model imports it back, so it
registers the full graph exactly once with no cycle. These tests assert that
property directly rather than enumerating relationships, which would rot.

Each case runs in a subprocess because the SQLAlchemy class registry is
process-global: an import performed by any earlier test would mask the result.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

# Modules that reach the database, each reproducing a real entry point's imports.
ENTRY_POINTS = [
    # The chokepoint itself, imported alone.
    "backend.core.db",
    # The API process.
    "backend.api.v1.api",
    # The container's startup script, which reaches models through User alone.
    "backend.startup_canonical_cutover",
    # The Celery workers.
    "backend.worker.tasks",
]

_SCRIPT = """
import importlib, sys
from sqlalchemy.orm import configure_mappers

importlib.import_module({module!r})
configure_mappers()
print("OK")
"""


@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_entry_point_can_configure_every_mapper(module):
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT.format(module=module)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"Importing {module} left part of the ORM graph unregistered, so this "
        "entry point cannot configure its mappers. Every model module must be "
        "reachable from backend/models/registry.py, which backend/core/db.py "
        "imports.\n\n" + (result.stderr or result.stdout)[-2500:]
    )
    assert "OK" in result.stdout


def test_importing_core_db_alone_registers_the_document_graph():
    """The specific closure that broke in production, pinned by name.

    A parametrised smoke test would still pass if these classes were quietly
    dropped from the registry and nothing yet referenced them.
    """
    script = """
import sys
from sqlmodel import SQLModel
import backend.core.db  # noqa: F401

registered = {mapper.class_.__name__ for mapper in SQLModel._sa_registry.mappers}
missing = [
    name
    for name in ("Document", "DocumentPage", "ContextChunk", "Recording", "Transcript")
    if name not in registered
]
if missing:
    raise SystemExit("missing from the class registry: " + ", ".join(missing))
print("OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, (result.stderr or result.stdout)[-2000:]
