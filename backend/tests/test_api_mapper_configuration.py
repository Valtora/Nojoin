"""Every model referenced by a relationship must register with its own module.

Most test modules import ``backend.models.registry``, which registers every
model and therefore hides a missing registration. Several real entry points do
not import it -- ``backend/startup_canonical_cutover.py`` reaches models through
``User`` alone, and the API process reaches them through ``recording_public`` --
so a model that is only reachable via the registry passes the entire suite and
then fails at container start with:

    expression 'DocumentPage.page_number' failed to locate a name

The rule this enforces: importing a model module must also register every model
its own relationships name by string. Each case runs in a subprocess because the
SQLAlchemy class registry is process-global, so any earlier import in this
process would mask the failure.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

# (module to import, class names its relationships reference by string)
RELATIONSHIP_CLOSURES = [
    ("backend.models.document", ["Document", "DocumentPage"]),
    ("backend.models.context_chunk", ["ContextChunk"]),
    ("backend.models.recording", ["Recording"]),
]

_SCRIPT = """
import importlib, sys
from sqlmodel import SQLModel

importlib.import_module({module!r})

assert "backend.models.registry" not in sys.modules, (
    "importing {module} must not pull in the registry; that would make this "
    "check vacuous"
)

registered = set()
for mapper in SQLModel._sa_registry.mappers:
    registered.add(mapper.class_.__name__)

missing = [name for name in {names!r} if name not in registered]
if missing:
    raise SystemExit("missing from the class registry: " + ", ".join(missing))
print("OK")
"""


@pytest.mark.parametrize(
    "module,names", RELATIONSHIP_CLOSURES, ids=[m for m, _ in RELATIONSHIP_CLOSURES]
)
def test_importing_a_model_registers_its_relationship_targets(module, names):
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT.format(module=module, names=names)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"Importing {module} on its own left part of its relationship graph "
        "unregistered, so any entry point that does not import "
        "backend/models/registry.py will fail to configure mappers.\n\n"
        + (result.stderr or result.stdout)[-2000:]
    )
