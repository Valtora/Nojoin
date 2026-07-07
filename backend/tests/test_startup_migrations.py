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
