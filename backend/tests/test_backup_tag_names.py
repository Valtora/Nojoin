"""A restore keeps each tag name once per user, in every conflict mode.

Tags and people tags hold each name once per user (revision edb5059e5d68). An
archive made before that can hold a name twice, and a target can already hold
an archived name. Neither may fail the restore part way: a copy of a name the
restore has already placed, in this archive or in the target, is matched to
that tag, and its sub-tags, meetings, tasks and people follow it. Runs on the
surrogate tables in ``backup_harness``, which carry the unique index.

Users: alice (1) and bob (2). Every row is Alice's unless it says otherwise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest
from sqlmodel import Session, select

from backend.tests.backup_harness import (
    TestGlobalSpeaker,
    TestPeopleTag,
    TestPeopleTagLink,
    TestRecording,
    TestRecordingTag,
    TestTag,
    TestUser,
    TestUserTask,
    TestUserTaskTag,
    build_test_context,
    patch_backup_manager,
)
from backend.tests.test_backup_legacy_owners import _restore, _row, _write_archive

# id, name, owner, parent
RECORDING_TAGS = [
    (1, "Board", 1, None),
    (2, "Board", 1, 3),  # a copy under Projects
    (3, "Projects", 1, None),
    (4, "Minutes", 1, 2),  # under the copy
    (5, "Board", 2, None),  # Bob's
    (6, "Client", 1, 7),  # a copy under its own copy
    (7, "Client", 1, None),
    (8, "Renewals", 1, 6),
]
# link id, recording, tag
RECORDING_LINKS = [(1, 10, 1), (2, 10, 2), (3, 11, 2), (4, 11, 4), (5, 11, 6)]
# link id, task, tag
TASK_LINKS = [(1, 1, 1), (2, 1, 2)]
PEOPLE_TAGS = [
    (1, "Client", 1, None),
    (2, "Client", 1, None),
    (3, "Key accounts", 1, 2),
]
# link id, person, tag
PEOPLE_LINKS = [(1, 101, 1), (2, 101, 2), (3, 102, 2)]

MODES = pytest.mark.parametrize(
    "modes",
    [{}, {"overwrite_existing": True}, {"clear_existing": True}],
    ids=["skip", "overwrite", "clear"],
)


def _archive_tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "users": [
            _row(id=user_id, username=username, hashed_password="x", settings={})
            for user_id, username in ((1, "alice"), (2, "bob"))
        ],
        "recordings": [
            _row(
                id=recording_id,
                name=f"Meeting {recording_id}",
                meeting_uid=f"tag-names-{recording_id}",
                audio_path=f"recordings/tag-names-{recording_id}.opus",
                status="PROCESSED",
                user_id=1,
            )
            for recording_id in (10, 11)
        ],
        "user_tasks": [_row(id=1, title="Follow up", user_id=1)],
        "global_speakers": [
            _row(id=person_id, name=name, color="teal", user_id=1)
            for person_id, name in ((101, "Alice Example"), (102, "Bob Example"))
        ],
        "tags": [
            _row(id=tag_id, name=name, color="blue", user_id=owner, parent_id=parent)
            for tag_id, name, owner, parent in RECORDING_TAGS
        ],
        "recording_tags": [
            _row(id=link_id, recording_id=recording_id, tag_id=tag_id)
            for link_id, recording_id, tag_id in RECORDING_LINKS
        ],
        "user_task_tags": [
            _row(id=link_id, task_id=task_id, tag_id=tag_id)
            for link_id, task_id, tag_id in TASK_LINKS
        ],
        "p_tags": [
            _row(id=tag_id, name=name, color="blue", user_id=owner, parent_id=parent)
            for tag_id, name, owner, parent in PEOPLE_TAGS
        ],
        "people_tag_links": [
            _row(id=link_id, global_speaker_id=person_id, tag_id=tag_id)
            for link_id, person_id, tag_id in PEOPLE_LINKS
        ],
    }


def _library(engine) -> dict[str, Any]:
    """Every tag by owner and name, with its parent's name, and every link."""
    with Session(engine) as session:
        users = {u.id: u.username for u in session.exec(select(TestUser)).all()}

        def tree(model) -> tuple[list[tuple], dict[int, str]]:
            rows = session.exec(select(model)).all()
            names = {row.id: row.name for row in rows}
            return (
                sorted(
                    (users[row.user_id], row.name, names.get(row.parent_id))
                    for row in rows
                ),
                names,
            )

        tags, tag_names = tree(TestTag)
        people_tags, people_tag_names = tree(TestPeopleTag)
        meetings = {
            r.id: r.meeting_uid for r in session.exec(select(TestRecording)).all()
        }
        tasks = {t.id: t.title for t in session.exec(select(TestUserTask)).all()}
        people = {p.id: p.name for p in session.exec(select(TestGlobalSpeaker)).all()}
        return {
            "tags": tags,
            "people_tags": people_tags,
            "recording_tags": sorted(
                (meetings[link.recording_id], tag_names[link.tag_id])
                for link in session.exec(select(TestRecordingTag)).all()
            ),
            "user_task_tags": sorted(
                (tasks[link.task_id], tag_names[link.tag_id])
                for link in session.exec(select(TestUserTaskTag)).all()
            ),
            "people_tag_links": sorted(
                (people[link.global_speaker_id], people_tag_names[link.tag_id])
                for link in session.exec(select(TestPeopleTagLink)).all()
            ),
        }


def _tree(
    *rows: tuple[str, str, Optional[str]],
) -> list[tuple[str, str, Optional[str]]]:
    return sorted(rows)


EXPECTED = {
    # The first copy of a name the restore places is the one the rest join.
    "tags": _tree(
        ("alice", "Board", None),
        ("alice", "Projects", None),
        ("alice", "Minutes", "Board"),
        ("alice", "Client", None),
        ("alice", "Renewals", "Client"),
        ("bob", "Board", None),
    ),
    "people_tags": _tree(
        ("alice", "Client", None),
        ("alice", "Key accounts", "Client"),
    ),
    "recording_tags": [
        ("tag-names-10", "Board"),
        ("tag-names-11", "Board"),
        ("tag-names-11", "Client"),
        ("tag-names-11", "Minutes"),
    ],
    "user_task_tags": [("Follow up", "Board")],
    "people_tag_links": [
        ("Alice Example", "Client"),
        ("Bob Example", "Client"),
    ],
}


@pytest.fixture
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, context)
    return context


def _seed_target(engine) -> None:
    """Alice already holds Projects and a Client people tag, in other colours."""
    with Session(engine) as session:
        alice = TestUser(username="alice", hashed_password="existing")
        session.add(alice)
        session.flush()
        session.add_all(
            [
                TestTag(name="Projects", color="green", user_id=alice.id),
                TestPeopleTag(name="Client", color="green", user_id=alice.id),
            ]
        )
        session.commit()


@pytest.mark.anyio
@MODES
async def test_an_archive_holding_a_name_twice_restores_it_once(
    tmp_path: Path, target, modes: dict[str, bool]
) -> None:
    zip_path = _write_archive(tmp_path / "old.zip", _archive_tables())

    job = await _restore(zip_path, "tag-names", **modes)

    assert (job["status"], job["warnings"]) == ("completed", {"skipped": {}})
    assert _library(target.sync_engine) == EXPECTED
    await target.async_engine.dispose()


@pytest.mark.anyio
@MODES
async def test_names_the_target_holds_take_the_archived_copies(
    tmp_path: Path, target, modes: dict[str, bool]
) -> None:
    _seed_target(target.sync_engine)
    zip_path = _write_archive(tmp_path / "old.zip", _archive_tables())

    job = await _restore(zip_path, "tag-names-target", **modes)

    assert (job["status"], job["warnings"]) == ("completed", {"skipped": {}})
    assert _library(target.sync_engine) == EXPECTED
    with Session(target.sync_engine) as session:
        colours = {
            (type(row).__name__, row.name): row.color
            for model in (TestTag, TestPeopleTag)
            for row in session.exec(select(model)).all()
            if row.name in ("Projects", "Client")
        }
    # Clear empties the target first; the other modes keep the target's tag.
    kept = "blue" if modes.get("clear_existing") else "green"
    assert colours == {
        ("TestTag", "Projects"): kept,
        ("TestTag", "Client"): "blue",
        ("TestPeopleTag", "Client"): kept,
    }
    await target.async_engine.dispose()
