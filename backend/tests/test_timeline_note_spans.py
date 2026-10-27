"""Line span snapshots remain unquoted, movable and constrained at the boundary."""

import importlib
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

from backend.core.db import driver_urls
from backend.models.timeline_note import TIMELINE_NOTE_SHAPE_CHECK
from backend.models.timeline_note_public import TIMELINE_MS_MAX, TimelineNoteCreate
from backend.tests.test_timeline_notes_api import BASE, _create
from backend.tests.test_timeline_notes_api import app as app
from backend.tests.test_timeline_notes_api import client as client
from backend.tests.test_timeline_notes_api import edge_refreshes as edge_refreshes
from backend.tests.test_timeline_notes_api import session_maker as session_maker


@pytest.mark.anyio
async def test_span_point_moves_its_snapshot_and_preserves_note_revision(
    client: AsyncClient,
) -> None:
    note = await _create(
        client, kind="point", anchor_ms=2000, start_ms=2000, end_ms=3500
    )
    assert note["quoted_text"] is None
    response = await client.patch(
        f"{BASE}/{note['id']}",
        json={"expected_revision": note["revision"], "anchor_ms": 0},
    )
    assert response.status_code == 200, response.text
    moved = response.json()
    assert (moved["anchor_ms"], moved["start_ms"], moved["end_ms"]) == (0, 0, 1500)
    assert moved["quoted_text"] is None and moved["kind"] == "point"
    assert moved["revision"] == note["revision"] + 1
    stale = await client.patch(
        f"{BASE}/{note['id']}", json={"expected_revision": 1, "anchor_ms": 5000}
    )
    assert stale.status_code == 409
    repeated = await client.patch(
        f"{BASE}/{note['id']}",
        json={"expected_revision": moved["revision"], "anchor_ms": 0},
    )
    assert repeated.json()["revision"] == moved["revision"]


@pytest.mark.anyio
async def test_span_move_overflow_refuses_without_changing_note(
    client: AsyncClient,
) -> None:
    note = await _create(client, kind="point", anchor_ms=0, start_ms=0, end_ms=1500)
    response = await client.patch(
        f"{BASE}/{note['id']}",
        json={"expected_revision": 1, "anchor_ms": TIMELINE_MS_MAX},
    )
    assert response.status_code == 422
    stored = (await client.get(BASE)).json()[0]
    assert (stored["revision"], stored["anchor_ms"], stored["end_ms"]) == (1, 0, 1500)


@pytest.mark.parametrize(
    "bounds",
    [
        {"start_ms": 0},
        {"end_ms": 2},
        {"start_ms": 0, "end_ms": -1},
        {"start_ms": 1, "end_ms": 2},
        {"start_ms": 0, "end_ms": 2, "quoted_text": "quote"},
        {"start_ms": 0, "end_ms": TIMELINE_MS_MAX + 1},
    ],
)
def test_point_span_rejects_incomplete_or_inconsistent_shape(
    bounds: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        TimelineNoteCreate(kind="point", body="note", anchor_ms=0, **bounds)


@pytest.mark.anyio
async def test_zero_duration_line_span_remains_movable(client: AsyncClient) -> None:
    note = await _create(client, kind="point", anchor_ms=20, start_ms=20, end_ms=20)
    moved = await client.patch(
        f"{BASE}/{note['id']}", json={"expected_revision": 1, "anchor_ms": 50}
    )
    assert moved.status_code == 200, moved.text
    assert (moved.json()["start_ms"], moved.json()["end_ms"]) == (50, 50)


@pytest.fixture(params=["sqlite", "postgresql"])
def span_engine(request: pytest.FixtureRequest) -> Iterator[Engine]:
    schema = None
    if request.param == "postgresql":
        schema = f"point_span_migration_{uuid4().hex[:12]}"
        engine = create_engine(
            driver_urls(request.getfixturevalue("postgres_test_url"))[1],
            connect_args={"options": f"-csearch_path={schema},public"},
        )
    else:
        engine = create_engine("sqlite://")
    try:
        if schema:
            with engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        yield engine
    finally:
        if schema:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_span_migration_preserves_old_notes_and_enforces_shape(
    span_engine: Engine,
) -> None:
    migration = importlib.import_module(
        "backend.alembic.versions.a6b9d2e4f810_point_note_spans"
    )
    assert migration.NEW_SHAPE == TIMELINE_NOTE_SHAPE_CHECK
    with span_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE timeline_notes (id INTEGER PRIMARY KEY, kind TEXT NOT NULL,"
                " anchor_ms INTEGER, start_ms INTEGER, end_ms INTEGER, quoted_text TEXT,"
                f" CONSTRAINT ck_timeline_notes_shape CHECK ({migration.OLD_SHAPE}))"
            )
        )
        connection.execute(
            text("INSERT INTO timeline_notes VALUES (1, 'point', 20, NULL, NULL, NULL)")
        )
        connection.execute(
            text(
                "INSERT INTO timeline_notes VALUES (3, 'selection', NULL, 10, 15, 'words')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO timeline_notes VALUES (4, 'head', NULL, NULL, NULL, NULL)"
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        connection.execute(
            text("INSERT INTO timeline_notes VALUES (2, 'point', 30, 30, 50, NULL)")
        )
        connection.execute(
            text("INSERT INTO timeline_notes VALUES (5, 'point', 40, 40, 40, NULL)")
        )
        for invalid in (
            "(6, 'point', 30, NULL, 50, NULL)",
            "(6, 'point', 30, 30, 50, 'quote')",
            "(6, 'point', 30, 31, 50, NULL)",
            "(6, 'selection', NULL, 10, 15, NULL)",
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text(f"INSERT INTO timeline_notes VALUES {invalid}"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.execute(
            text(
                "SELECT id, anchor_ms, start_ms, end_ms FROM timeline_notes ORDER BY id"
            )
        ).all() == [
            (1, 20, None, None),
            (2, 30, None, None),
            (3, None, 10, 15),
            (4, None, None, None),
            (5, 40, None, None),
        ]
