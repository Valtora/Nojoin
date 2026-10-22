"""An archive made before 7cee4b4388a7 restores as the upgraded install would.

Such an archive can hold people and people tags with no owner, a speaker linked
to another user's person, a person linked to another user's people tag, and a
tag nested under another user's. The restore gives them owners and keeps each
link and parent within one library, by the rules of 7cee4b4388a7 and
f704bbd87f98, instead of skipping the rows with no owner. Runs on the surrogate
tables in ``backup_harness``; ``test_backup_legacy_owners_postgres.py`` holds the
rules to the real migrations.

Users: alice (1), bob (2), cara (3, the install's Owner). Person names are made
up.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select

from backend.core.backup import BackupManager
from backend.core.backup.restore.legacy_owners import plan_owner_repairs
from backend.tests.backup_harness import (
    TestGlobalSpeaker,
    TestPeopleTag,
    TestPeopleTagLink,
    TestRecordingSpeaker,
    TestRecordingSpeakerDriftRefusal,
    TestTag,
    TestUser,
    build_test_context,
    patch_backup_manager,
)

STAMP = "2026-09-01T10:00:00"
USERS = [(1, "alice", "user"), (2, "bob", "user"), (3, "cara", "owner")]
# recording id -> owner
RECORDINGS = {10: 1, 11: 1, 20: 2, 30: 3, 40: None}
# id, name, owner
PEOPLE = [
    (101, "Ann Example", None),  # Alice's recordings only
    (102, "Ben Example", None),  # two links from Alice's, one from Bob's
    (103, "Cy Example", None),  # linked from nothing
    (104, "Dee Example", 1),  # Alice's, linked from Bob's too
    (105, "Dee Example", 2),  # Bob's own
    (106, "Fay Example", None),  # Cara's recordings, and Cara has a Fay
    (107, "Fay Example", 3),
    (108, "Gus Example", None),  # one link each from Bob's and Cara's
    (109, "Hal Example", None),  # linked only from a recording with no owner
]
# speaker id, recording, person, merged into
SPEAKERS = [
    (1, 10, 101, None),
    (2, 10, 102, None),
    (3, 11, 102, None),
    (4, 20, 102, None),
    (5, 10, 104, None),
    (6, 20, 104, None),
    (7, 20, 104, 6),
    (8, 30, 106, None),
    (9, 20, 108, None),
    (10, 30, 108, None),
    (11, 40, 109, None),
]
# id, name, owner, parent
PEOPLE_TAGS = [
    (1, "Client", None, None),  # used by two of Alice's people and one of Bob's
    (2, "Board", None, None),  # unused, above a tag of Cara's
    (3, "Advisers", 3, 2),
    (4, "Unused", None, None),  # used by nobody, and Cara has one
    (5, "Vendor", 2, None),  # Bob's, used by one of Alice's people
    (6, "Nested", 1, 5),  # Alice's, under Bob's
    (7, "Unused", 3, None),
    (8, "Key accounts", 2, 1),  # Bob's, under the tag Alice is given
]
# link id, person, tag
TAG_LINKS = [(1, 101, 1), (2, 104, 1), (3, 105, 1), (4, 101, 5)]
# id, name, owner, parent
RECORDING_TAGS = [
    (50, "Hiring", 2, None),
    (51, "Cross", 1, 50),  # Alice's, under Bob's
    (52, "Board meetings", 1, None),
    (53, "Minutes", 1, 52),
]


def _row(**fields: Any) -> dict[str, Any]:
    return {"created_at": STAMP, "updated_at": STAMP, **fields}


def _archive_tables() -> dict[str, list[dict[str, Any]]]:
    """The members of an archive made by 2.10.0, before either revision."""
    return {
        "users": [
            _row(
                id=user_id,
                username=username,
                hashed_password="x",
                role=role,
                is_superuser=role == "owner",
                settings={},
            )
            for user_id, username, role in USERS
        ],
        "recordings": [
            _row(
                id=recording_id,
                name=f"Meeting {recording_id}",
                meeting_uid=f"legacy-owner-{recording_id}",
                audio_path=f"recordings/legacy-owner-{recording_id}.opus",
                status="PROCESSED",
                user_id=owner,
            )
            for recording_id, owner in RECORDINGS.items()
        ],
        "global_speakers": [
            _row(
                id=person_id,
                name=name,
                color="teal",
                embedding=[0.6, 0.8],
                email=f"person-{person_id}@example.com",
                user_id=owner,
            )
            for person_id, name, owner in PEOPLE
        ],
        "recording_speakers": [
            _row(
                id=speaker_id,
                recording_id=recording_id,
                global_speaker_id=person_id,
                diarization_label=f"SPEAKER_{speaker_id:02d}",
                merged_into_id=merged_into,
            )
            for speaker_id, recording_id, person_id, merged_into in SPEAKERS
        ],
        "recording_speaker_drift_refusals": [
            {"recording_speaker_id": 1, "global_speaker_id": 101},
            {"recording_speaker_id": 6, "global_speaker_id": 104},
        ],
        "p_tags": [
            _row(id=tag_id, name=name, color="blue", user_id=owner, parent_id=parent)
            for tag_id, name, owner, parent in PEOPLE_TAGS
        ],
        "people_tag_links": [
            _row(id=link_id, global_speaker_id=person_id, tag_id=tag_id)
            for link_id, person_id, tag_id in TAG_LINKS
        ],
        "tags": [
            _row(id=tag_id, name=name, user_id=owner, parent_id=parent)
            for tag_id, name, owner, parent in RECORDING_TAGS
        ],
    }


def _write_archive(path: Path, tables: dict[str, list[dict[str, Any]]]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "backup_info.json", json.dumps({"format_version": 3, "version": "2.10.0"})
        )
        for table_name, rows in tables.items():
            archive.writestr(f"{table_name}.json", json.dumps(rows))
    return path


async def _restore(zip_path: Path, job_id: str, **modes: bool) -> dict[str, Any]:
    BackupManager.restore_jobs[job_id] = {
        "status": "pending",
        "progress": "Queued",
        "error": None,
    }
    await BackupManager.restore_backup(job_id, str(zip_path), **modes)
    return BackupManager.restore_jobs[job_id]


def _library(engine) -> dict[str, Any]:
    """The restored people, tags and links, named rather than numbered."""
    with Session(engine) as session:
        users = {u.id: u.username for u in session.exec(select(TestUser)).all()}
        people = {
            p.id: (users.get(p.user_id), p.name)
            for p in session.exec(select(TestGlobalSpeaker)).all()
        }
        tags = {
            t.id: (users.get(t.user_id), t.name, t.parent_id)
            for t in session.exec(select(TestPeopleTag)).all()
        }
        recording_tags = {
            t.id: (users.get(t.user_id), t.name, t.parent_id)
            for t in session.exec(select(TestTag)).all()
        }
        speakers = {
            s.id: (s.diarization_label, s.global_speaker_id)
            for s in session.exec(select(TestRecordingSpeaker)).all()
        }
        refusals = session.exec(select(TestRecordingSpeakerDriftRefusal)).all()
        links = session.exec(select(TestPeopleTagLink)).all()

    def tag_key(rows: dict, tag_id: int | None) -> tuple | None:
        return None if tag_id is None else rows[tag_id][:2]

    return {
        "people": sorted(people.values()),
        "speakers": {
            label: people.get(person_id) for label, person_id in speakers.values()
        },
        "refusals": sorted(
            (speakers[r.recording_speaker_id][0], people[r.global_speaker_id])
            for r in refusals
        ),
        "people_tags": {row[:2]: tag_key(tags, row[2]) for row in tags.values()},
        "tag_links": sorted(
            (people[link.global_speaker_id], tags[link.tag_id][:2]) for link in links
        ),
        "recording_tags": {
            row[:2]: tag_key(recording_tags, row[2]) for row in recording_tags.values()
        },
    }


EXPECTED_PEOPLE = sorted(
    [
        ("alice", "Ann Example"),
        ("alice", "Ben Example"),  # most links are Alice's
        ("alice", "Dee Example"),
        ("bob", "Ben Example"),  # made for Bob's link to Alice's Ben
        ("bob", "Dee Example"),
        ("bob", "Gus Example"),  # a tie goes to the lowest user id
        ("cara", "Fay Example"),
        ("cara", "Fay Example (2)"),  # Cara already had a Fay
        ("cara", "Gus Example"),  # made for Cara's link to Bob's Gus
    ]
)
EXPECTED_SPEAKERS = {
    "SPEAKER_01": ("alice", "Ann Example"),
    "SPEAKER_02": ("alice", "Ben Example"),
    "SPEAKER_03": ("alice", "Ben Example"),
    "SPEAKER_04": ("bob", "Ben Example"),
    "SPEAKER_05": ("alice", "Dee Example"),
    "SPEAKER_06": ("bob", "Dee Example"),
    "SPEAKER_07": ("bob", "Dee Example"),
    "SPEAKER_08": ("cara", "Fay Example (2)"),
    "SPEAKER_09": ("bob", "Gus Example"),
    "SPEAKER_10": ("cara", "Gus Example"),
}
EXPECTED_PEOPLE_TAGS = {
    ("alice", "Client"): None,  # most uses are Alice's
    ("alice", "Vendor"): None,  # made for Alice's person's link to Bob's
    ("alice", "Nested"): None,  # was under Bob's Vendor
    ("bob", "Client"): None,  # made for Bob's person's link to Alice's
    ("bob", "Key accounts"): ("bob", "Client"),  # was under Alice's Client
    ("bob", "Vendor"): None,
    ("cara", "Board"): None,  # the tag above Cara's Advisers
    ("cara", "Advisers"): ("cara", "Board"),
    ("cara", "Unused"): None,
    ("cara", "Unused (2)"): None,  # used by nobody, given to the Owner
}
EXPECTED_TAG_LINKS = sorted(
    [
        (("alice", "Ann Example"), ("alice", "Client")),
        (("alice", "Ann Example"), ("alice", "Vendor")),
        (("alice", "Dee Example"), ("alice", "Client")),
        (("bob", "Dee Example"), ("bob", "Client")),
    ]
)
EXPECTED_RECORDING_TAGS = {
    ("bob", "Hiring"): None,
    ("alice", "Cross"): None,  # was under Bob's Hiring
    ("alice", "Board meetings"): None,
    ("alice", "Minutes"): ("alice", "Board meetings"),
}


@pytest.fixture
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, context)
    return context


@pytest.mark.anyio
async def test_an_old_archive_restores_with_every_person_and_tag_owned(
    tmp_path: Path, target
) -> None:
    zip_path = _write_archive(tmp_path / "old.zip", _archive_tables())

    job = await _restore(zip_path, "legacy-owners")

    # Only the people no owned recording links stay without an owner, as on
    # the upgraded install, and the restore skips them, as it skips the
    # recording with no owner and its speaker.
    assert job["status"] == "completed_with_warnings"
    assert job["warnings"]["skipped"] == {
        "recordings": {"unresolved_owner": 1},
        "global_speakers": {"unresolved_owner": 2},
        "recording_speakers": {"unresolved_owner": 1},
    }
    library = _library(target.sync_engine)
    assert library["people"] == EXPECTED_PEOPLE
    assert library["speakers"] == EXPECTED_SPEAKERS
    # A refusal stays with the person it was made for, as the migration leaves it.
    assert library["refusals"] == [
        ("SPEAKER_01", ("alice", "Ann Example")),
        ("SPEAKER_06", ("alice", "Dee Example")),
    ]
    assert library["people_tags"] == EXPECTED_PEOPLE_TAGS
    assert library["tag_links"] == EXPECTED_TAG_LINKS
    assert library["recording_tags"] == EXPECTED_RECORDING_TAGS

    # Each person given an owner keeps their voiceprint and details; the
    # people made for moved links carry a name and colour only.
    with Session(target.sync_engine) as session:
        details = {
            (p.user_id, p.name): (p.embedding, p.email, p.color)
            for p in session.exec(select(TestGlobalSpeaker)).all()
        }
        users = {u.username: u.id for u in session.exec(select(TestUser)).all()}
    assert details[(users["cara"], "Fay Example (2)")] == (
        [0.6, 0.8],
        "person-106@example.com",
        "teal",
    )
    assert details[(users["bob"], "Ben Example")] == (None, None, "teal")
    await target.async_engine.dispose()


@pytest.mark.anyio
async def test_a_merge_leaves_other_users_alone_and_reuses_a_users_own_person(
    tmp_path: Path, target
) -> None:
    with Session(target.sync_engine) as session:
        bob = TestUser(username="bob", hashed_password="existing")
        dave = TestUser(username="dave", hashed_password="existing")
        session.add_all([bob, dave])
        session.flush()
        session.add_all(
            [
                TestGlobalSpeaker(
                    name="Ben Example",
                    color="amber",
                    email="ben@example.org",
                    user_id=bob.id,
                ),
                TestGlobalSpeaker(name="Ann Example", color="red", user_id=dave.id),
                TestPeopleTag(name="Client", color="red", user_id=dave.id),
            ]
        )
        session.commit()
        bob_id, dave_id = bob.id, dave.id

    zip_path = _write_archive(tmp_path / "old.zip", _archive_tables())
    await _restore(zip_path, "legacy-owners-merge")
    library = _library(target.sync_engine)

    # Bob's link goes to the Ben he already had, and the merge fills only
    # what his Ben was missing.
    assert library["speakers"]["SPEAKER_04"] == ("bob", "Ben Example")
    with Session(target.sync_engine) as session:
        bobs_ben = session.exec(
            select(TestGlobalSpeaker).where(TestGlobalSpeaker.user_id == bob_id)
        ).all()
        daves = session.exec(
            select(TestGlobalSpeaker).where(TestGlobalSpeaker.user_id == dave_id)
        ).all()
        daves_tags = session.exec(
            select(TestPeopleTag).where(TestPeopleTag.user_id == dave_id)
        ).all()
    ben = next(p for p in bobs_ben if p.name == "Ben Example")
    assert (ben.color, ben.email, ben.embedding) == ("amber", "ben@example.org", None)
    assert [(p.name, p.color) for p in daves] == [("Ann Example", "red")]
    assert [(t.name, t.color) for t in daves_tags] == [("Client", "red")]
    assert library["people"] == sorted(EXPECTED_PEOPLE + [("dave", "Ann Example")])

    # A second restore of the same archive finds every row it made.
    await _restore(zip_path, "legacy-owners-again")
    assert _library(target.sync_engine) == library
    await target.async_engine.dispose()


def test_an_archive_made_since_needs_no_repair(tmp_path: Path) -> None:
    old = _write_archive(tmp_path / "old.zip", _archive_tables())
    with zipfile.ZipFile(old) as archive:
        repairs = plan_owner_repairs(archive)
    assert repairs.tables

    # The repaired rows are what an archive of the upgraded install holds,
    # and planning them again finds nothing to do.
    repaired = {
        table_name: repairs.apply(table_name, rows)
        for table_name, rows in _archive_tables().items()
    }
    upgraded = _write_archive(tmp_path / "upgraded.zip", repaired)
    with zipfile.ZipFile(upgraded) as archive:
        assert plan_owner_repairs(archive).tables == {}
