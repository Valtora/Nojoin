"""Every datetime column in the production schema stores naive UTC.

Nojoin writes naive UTC throughout (``backend.utils.time.utc_now``), and every migration
creates its datetime columns as ``sa.DateTime()``, which is ``timezone=False``. From
sqlmodel 0.0.45, a field annotated ``datetime`` with no ``sa_type`` or ``sa_column`` maps
to ``UTCDateTime`` instead: a timezone-aware column type that raises ``ValueError`` on
every naive value bound to it. A field declared that way disagrees with its own
migration and fails on its first insert, so declare it with ``sa_type=DateTime``.

Moving the schema to aware UTC is a deliberate migration of every datetime column and
every producer, not something one new field should start on its own.
"""

from __future__ import annotations

import importlib
import pkgutil

from sqlmodel import SQLModel

import backend.models
import backend.models.registry  # noqa: F401  (registers every model with the metadata)


def _production_datetime_columns() -> dict[str, bool]:
    """Map ``table.column`` to its ``timezone`` flag for every datetime column.

    Read from the production mappers, not ``SQLModel.metadata``, for the reason given in
    test_backup_model_parity: surrogate test tables register there too.
    """
    for module in pkgutil.iter_modules(backend.models.__path__):
        importlib.import_module(f"backend.models.{module.name}")

    columns: dict[str, bool] = {}
    for mapper in SQLModel._sa_registry.mappers:
        model = mapper.class_
        if not getattr(model, "__module__", "").startswith("backend.models."):
            continue
        for column in model.__table__.columns:
            # UTCDateTime is a TypeDecorator, which proxies ``timezone`` to its impl.
            timezone = getattr(column.type, "timezone", None)
            if timezone is not None:
                columns[f"{model.__tablename__}.{column.name}"] = bool(timezone)
    return columns


def test_every_datetime_column_is_naive() -> None:
    columns = _production_datetime_columns()
    assert columns, "found no datetime columns, so this check tested nothing"

    aware = sorted(name for name, timezone in columns.items() if timezone)
    assert not aware, (
        "These columns are timezone-aware, but the schema and every writer use naive "
        "UTC. Declare each field with sa_type=DateTime: " + ", ".join(aware)
    )
