"""Central registry of all ORM model modules.

Importing this module imports every module that defines a ``table=True``
SQLModel class, which is what registers those classes on
``SQLModel.metadata``. Two consumers depend on that side effect:

* ``backend/alembic/env.py`` assigns ``target_metadata = SQLModel.metadata``
  for ``alembic revision --autogenerate``. If the metadata is empty,
  autogenerate can emit ``DROP TABLE`` for existing tables or miss new
  columns.
* ``backend/init_db.py`` calls ``SQLModel.metadata.create_all(...)``. Without
  these imports a fresh database is created with only the handful of tables
  reachable through other imports, so later code fails with missing-table
  errors.

Ruff's F401 (unused-import) is disabled for this module in ``pyproject.toml``
because these imports are intentional registration seams, not dead code.
"""

from backend.models import (
    base,
    calendar,
    chat,
    context_chunk,
    document,
    invitation,
    people_tag,
    pipeline,
    recording,
    revoked_jwt,
    speaker,
    tag,
    task,
    transcript,
    user,
)

__all__ = [
    "base",
    "calendar",
    "chat",
    "context_chunk",
    "document",
    "invitation",
    "people_tag",
    "pipeline",
    "recording",
    "revoked_jwt",
    "speaker",
    "tag",
    "task",
    "transcript",
    "user",
]
