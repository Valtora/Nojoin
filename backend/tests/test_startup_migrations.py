from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from backend import startup_migrations


def _write_migration(
    path: Path, revision: str, down_revision: str | tuple[str, ...] | None
) -> None:
    path.write_text(
        "\n".join(
            [
                f"revision = '{revision}'",
                f"down_revision = {down_revision!r}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_get_head_revision_ids_uses_checked_in_graph(tmp_path: Path) -> None:
    _write_migration(tmp_path / "a1.py", "a1", None)
    _write_migration(tmp_path / "b2.py", "b2", "a1")
    _write_migration(tmp_path / "c3.py", "c3", ("a1", "b2"))

    assert startup_migrations.get_head_revision_ids(tmp_path) == ("c3",)


def test_checked_in_migrations_have_single_head() -> None:
    # A forked graph (two heads) makes `alembic upgrade head` abort on boot, so
    # the real, checked-in migrations must always resolve to exactly one head.
    versions_dir = Path(startup_migrations.__file__).parent / "alembic" / "versions"
    heads = startup_migrations.get_head_revision_ids(versions_dir)

    assert len(heads) == 1, f"Expected a single migration head, found: {heads}"


def test_repair_orphaned_revision_state_raises_when_auto_repair_disabled() -> None:
    engine = create_engine("sqlite://")

    with engine.connect() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('deadbeef')")
        )
        connection.commit()

        with pytest.raises(RuntimeError, match="missing Alembic revision"):
            startup_migrations.repair_orphaned_revision_state(
                connection,
                auto_repair_enabled=False,
                known_revision_ids={"feedface"},
                head_revision_ids=("feedface",),
            )


def test_repair_orphaned_revision_state_restamps_current_heads_when_enabled() -> None:
    engine = create_engine("sqlite://")

    with engine.connect() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('deadbeef')")
        )
        connection.commit()

        repaired = startup_migrations.repair_orphaned_revision_state(
            connection,
            auto_repair_enabled=True,
            known_revision_ids={"feedface"},
            head_revision_ids=("feedface",),
        )
        current_revision_ids = (
            connection.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num")
            )
            .scalars()
            .all()
        )

    assert repaired == ("deadbeef",)
    assert current_revision_ids == ["feedface"]


def test_migrations_do_not_call_jsonb_array_length_in_a_predicate() -> None:
    """Guard a footgun that no other check can catch.

    Postgres does not guarantee that ``AND`` conditions are evaluated left to
    right, so a predicate like::

        WHERE jsonb_typeof(col) = 'array' AND jsonb_array_length(col) > 0

    can still run ``jsonb_array_length`` against a row holding JSON ``null``
    and abort the whole migration with "cannot get array length of a scalar".
    Several voiceprint columns hold JSON ``null`` rather than SQL ``NULL`` for
    cleared values, so this is reachable in practice -- it broke a real
    deployment.

    Use a plain comparison such as ``col <> '[]'::jsonb`` instead, which is
    safe whatever the stored value's type.

    Migrations are never executed against Postgres in CI (only the revision
    graph is validated), so a static check is the only gate available here.
    """
    versions_dir = Path(startup_migrations.__file__).parent / "alembic" / "versions"

    offenders = [
        path.name
        for path in sorted(versions_dir.glob("*.py"))
        if "jsonb_array_length" in path.read_text(encoding="utf-8")
    ]

    assert not offenders, (
        "These migrations call jsonb_array_length, which aborts on rows holding "
        f"JSON null regardless of any type guard: {offenders}. "
        "Use `col <> '[]'::jsonb` instead."
    )
