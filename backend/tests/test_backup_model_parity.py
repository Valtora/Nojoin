"""Static parity checks between the real database schema and the backup contract.

These tests reflect over the production models rather than the surrogate tables used by
``test_backup_manager``. That matters: the surrogates exist because the real models use
Postgres-specific column types that cannot be created on SQLite, and their divergence is
precisely what let several foreign-key bugs live undetected. The checks here read the
actual schema, so they cannot be fooled by a surrogate that omits a column, and they
cannot be fooled by SQLite's lax foreign-key enforcement either, because they never touch
a database at all.

The contract enforced here is simple. Every table either goes in a backup or is listed as
deliberately excluded with a reason. Every foreign key on an archived table is classified
as ownership, enrichment or deferred. Adding a column or a model without deciding which
it is makes these tests fail.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest
from sqlmodel import SQLModel

import backend.models
import backend.models.registry  # noqa: F401  (registers every model with the metadata)
from backend.core.backup.format import (
    DEFERRED_FOREIGN_KEYS,
    RESTORE_FOREIGN_KEYS,
    UNARCHIVED_TABLES,
)
from backend.core.backup.runtime import MODELS


def _real_table_names() -> set[str]:
    """Every table declared by a model under ``backend.models``.

    Deliberately not read from ``SQLModel.metadata``: the surrogate tables in
    test_backup_manager register themselves there too when both modules load in one
    session, and this check must only ever see the production schema.
    """
    for module in pkgutil.iter_modules(backend.models.__path__):
        importlib.import_module(f"backend.models.{module.name}")

    names: set[str] = set()
    for mapper in SQLModel._sa_registry.mappers:
        model = mapper.class_
        if getattr(model, "__module__", "").startswith("backend.models."):
            names.add(model.__tablename__)
    return names


REAL_TABLE_NAMES = _real_table_names()
ARCHIVED_MODELS_BY_KEY = dict(MODELS)
ARCHIVED_TABLE_NAMES = {model.__tablename__ for _, model in MODELS}


def _foreign_keys_for(model) -> list[tuple[str, str]]:
    """Return (column name, referenced table name) for every foreign key on a model."""
    found: list[tuple[str, str]] = []
    for column in model.__table__.columns:
        for foreign_key in column.foreign_keys:
            found.append((column.name, foreign_key.column.table.name))
    return found


def test_every_table_is_either_archived_or_explicitly_excluded() -> None:
    # A new model that nobody classified is the failure mode that lost documents: the
    # table simply never appeared in a backup and no test noticed.
    unclassified = sorted(
        table_name
        for table_name in REAL_TABLE_NAMES
        if table_name not in ARCHIVED_TABLE_NAMES
        and table_name not in UNARCHIVED_TABLES
    )

    assert not unclassified, (
        "These tables are neither backed up nor listed in UNARCHIVED_TABLES: "
        f"{unclassified}. Add them to MODELS, or record why they are excluded."
    )


def test_exclusion_list_does_not_name_tables_that_no_longer_exist() -> None:
    # Keeps the exclusion list honest as models are renamed or removed.
    stale = sorted(
        table_name
        for table_name in UNARCHIVED_TABLES
        if table_name not in REAL_TABLE_NAMES
    )

    assert not stale, f"UNARCHIVED_TABLES names tables that do not exist: {stale}"


def test_exclusion_list_gives_a_reason_for_every_entry() -> None:
    missing_reason = sorted(
        table_name
        for table_name, reason in UNARCHIVED_TABLES.items()
        if not reason.strip()
    )

    assert not missing_reason, (
        f"These exclusions have no stated reason: {missing_reason}"
    )


@pytest.mark.parametrize("table_key", sorted(ARCHIVED_MODELS_BY_KEY))
def test_every_foreign_key_on_an_archived_table_is_classified(table_key: str) -> None:
    # The regression guard for findings 1, 3, 6 and 7, and for the three canonical
    # pipeline back-references on recording_speakers. An unclassified foreign key is
    # carried across verbatim, which either violates the constraint (silently dropping
    # the row and everything beneath it) or attaches the row to an unrelated record.
    model = ARCHIVED_MODELS_BY_KEY[table_key]

    classified = {spec.column for spec in RESTORE_FOREIGN_KEYS.get(table_key, ())}
    classified.update(DEFERRED_FOREIGN_KEYS.get(table_key, ()))

    unclassified = sorted(
        column_name
        for column_name, _ in _foreign_keys_for(model)
        if column_name not in classified
    )

    assert not unclassified, (
        f"{model.__tablename__} has unclassified foreign keys: {unclassified}. "
        "Add each to RESTORE_FOREIGN_KEYS as an ownership or enrichment link, or to "
        "DEFERRED_FOREIGN_KEYS if it needs a second pass."
    )


@pytest.mark.parametrize("table_key", sorted(ARCHIVED_MODELS_BY_KEY))
def test_classification_matches_the_real_schema(table_key: str) -> None:
    # Catches a classification that has drifted from the column it claims to describe,
    # for example after a column rename or a changed foreign-key target.
    model = ARCHIVED_MODELS_BY_KEY[table_key]
    actual = dict(_foreign_keys_for(model))

    for spec in RESTORE_FOREIGN_KEYS.get(table_key, ()):
        assert spec.column in actual, (
            f"{model.__tablename__}.{spec.column} is classified but is not a foreign "
            "key on the real model"
        )

        target_model = ARCHIVED_MODELS_BY_KEY.get(spec.target_table)
        expected_table = (
            target_model.__tablename__ if target_model else spec.target_table
        )
        assert actual[spec.column] == expected_table, (
            f"{model.__tablename__}.{spec.column} is classified as pointing at "
            f"{expected_table}, but the schema points it at {actual[spec.column]}"
        )


def test_ownership_links_always_target_an_archived_table() -> None:
    # An ownership link to a table that is never archived can never resolve, so every
    # row carrying it would be skipped and the table would restore empty.
    broken: list[str] = []
    for table_key, specs in RESTORE_FOREIGN_KEYS.items():
        for spec in specs:
            if spec.ownership and spec.target_table not in ARCHIVED_MODELS_BY_KEY:
                broken.append(f"{table_key}.{spec.column} -> {spec.target_table}")

    assert not broken, (
        "These ownership links point at tables that are never restored, so every row "
        f"carrying them would be dropped: {sorted(broken)}"
    )


def test_deferred_columns_are_self_referential() -> None:
    # The deferred pass exists only for columns whose target lives in the same table and
    # may not be inserted yet. Anything else belongs in the forward pass.
    for table_key, columns in DEFERRED_FOREIGN_KEYS.items():
        model = ARCHIVED_MODELS_BY_KEY[table_key]
        actual = dict(_foreign_keys_for(model))
        for column in columns:
            assert actual.get(column) == model.__tablename__, (
                f"{table_key}.{column} is deferred but does not reference its own table"
            )
