"""Co-editing a shared meeting: what an editor changes, whose library it lands
in, and the notes conflict that keeps two editors from overwriting each other."""

from __future__ import annotations

import pytest
from sqlmodel import select

from backend.models.recording import Recording
from backend.models.recording_share import RecordingShare
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.timeline_note import TimelineNote
from backend.tests.sso_sharing_support import build_harness
from backend.utils.notes_revision import notes_revision


@pytest.fixture
async def harness():
    async for built in build_harness():
        yield built


@pytest.fixture
async def meeting(harness):
    owner = await harness.add_user("owner")
    editor = await harness.add_user("editor")
    viewer = await harness.add_user("viewer")
    recording = await harness.add_recording(owner)
    async with harness.session_maker() as session:
        session.add(
            RecordingShare(recording_id=recording.id, user_id=editor.id, level="edit")
        )
        session.add(
            RecordingShare(recording_id=recording.id, user_id=viewer.id, level="view")
        )
        alice = GlobalSpeaker(
            name="Alice",
            user_id=owner.id,
            email="alice@example.org",
            phone_number="+31 6 1234 5678",
            notes="Prefers mornings",
        )
        carol = GlobalSpeaker(name="Carol", user_id=owner.id, email="carol@example.org")
        session.add_all([alice, carol])
        await session.flush()
        session.add(
            RecordingSpeaker(
                recording_id=recording.id,
                diarization_label="SPEAKER_00",
                global_speaker_id=alice.id,
            )
        )
        session.add(
            RecordingSpeaker(
                recording_id=recording.id,
                diarization_label="SPEAKER_01",
                local_name="Dave",
            )
        )
        await session.commit()
        ids = {"alice": alice.id, "carol": carol.id}
    return owner, editor, viewer, recording, ids


def _notes_url(recording) -> str:
    return f"/api/v1/transcripts/{recording.public_id}/notes"


@pytest.mark.anyio
async def test_editor_saves_notes_and_the_owner_sees_them(harness, meeting) -> None:
    owner, editor, _, recording, _ = meeting
    async with harness.client(editor) as client:
        start = (await client.get(_notes_url(recording))).json()
        saved = await client.put(
            _notes_url(recording),
            json={
                "notes": "# Notes\nEditor was here",
                "base_revision": start["notes_revision"],
            },
        )
    assert saved.status_code == 200, saved.text
    assert saved.json()["notes_revision"] == notes_revision("# Notes\nEditor was here")
    async with harness.client(owner) as client:
        assert (await client.get(_notes_url(recording))).json()[
            "notes"
        ] == "# Notes\nEditor was here"


@pytest.mark.anyio
async def test_a_stale_save_gets_a_conflict_with_the_current_notes(
    harness, meeting
) -> None:
    owner, editor, _, recording, _ = meeting
    async with harness.client(editor) as client:
        base = (await client.get(_notes_url(recording))).json()["notes_revision"]
    async with harness.client(owner) as client:
        first = await client.put(
            _notes_url(recording),
            json={"notes": "owner's version", "base_revision": base},
        )
        assert first.status_code == 200
    async with harness.client(editor) as client:
        stale = await client.put(
            _notes_url(recording),
            json={"notes": "editor's version", "base_revision": base},
        )
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "notes_conflict"
    assert detail["notes"] == "owner's version"
    assert detail["notes_revision"] == notes_revision("owner's version")


@pytest.mark.anyio
async def test_a_save_without_a_base_overwrites_as_before(harness, meeting) -> None:
    owner, _, _, recording, _ = meeting
    async with harness.client(owner) as client:
        saved = await client.put(_notes_url(recording), json={"notes": "plain save"})
    assert saved.status_code == 200


@pytest.mark.anyio
async def test_viewer_cannot_edit(harness, meeting) -> None:
    _, _, viewer, recording, _ = meeting
    async with harness.client(viewer) as client:
        notes = await client.put(_notes_url(recording), json={"notes": "nope"})
        rename = await client.patch(
            f"/api/v1/recordings/{recording.public_id}", json={"name": "Renamed"}
        )
    assert notes.status_code == 403
    assert rename.status_code == 403


@pytest.mark.anyio
async def test_editor_renames_but_cannot_change_the_language(harness, meeting) -> None:
    _, editor, _, recording, _ = meeting
    url = f"/api/v1/recordings/{recording.public_id}"
    async with harness.client(editor) as client:
        renamed = await client.patch(url, json={"name": "Renamed by editor"})
        language = await client.patch(url, json={"transcription_language": "nl"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Renamed by editor"
    assert language.status_code == 403
    async with harness.session_maker() as session:
        stored = await session.get(Recording, recording.id)
    assert stored.transcription_language is None


@pytest.mark.anyio
async def test_owner_only_actions_stay_with_the_owner(harness, meeting) -> None:
    _, editor, _, recording, _ = meeting
    async with harness.client(editor) as client:
        generate = await client.post(
            f"/api/v1/transcripts/{recording.public_id}/notes/generate"
        )
        delete = await client.delete(f"/api/v1/recordings/{recording.public_id}")
        reprocess = await client.post(
            f"/api/v1/recordings/{recording.public_id}/reprocess"
        )
    # Not migrated to sharing: the owner check answers as for any stranger.
    assert generate.status_code in (404, 422)
    assert delete.status_code == 404
    assert reprocess.status_code in (404, 422)


@pytest.mark.anyio
async def test_editor_promotes_a_speaker_into_the_owners_library(
    harness, meeting
) -> None:
    owner, editor, _, recording, _ = meeting
    async with harness.client(editor) as client:
        response = await client.post(
            f"/api/v1/speakers/recordings/{recording.public_id}/speakers/SPEAKER_01/promote",
        )
    assert response.status_code in (200, 201), response.text
    async with harness.session_maker() as session:
        dave = (
            await session.execute(
                select(GlobalSpeaker).where(GlobalSpeaker.name == "Dave")
            )
        ).scalar_one()
    assert dave.user_id == owner.id
    assert dave.user_id != editor.id


@pytest.mark.anyio
async def test_editor_timeline_note_is_attributed_to_the_editor(
    harness, meeting
) -> None:
    _, editor, _, recording, _ = meeting
    async with harness.client(editor) as client:
        created = await client.post(
            f"/api/v1/transcripts/{recording.public_id}/timeline-notes",
            json={"kind": "point", "body": "Decision: ship it", "anchor_ms": 1000},
        )
    assert created.status_code == 201, created.text
    async with harness.session_maker() as session:
        note = (await session.execute(select(TimelineNote))).scalar_one()
    assert note.created_by_user_id == editor.id


@pytest.mark.anyio
async def test_speaker_candidates_offer_only_people_in_the_meeting(
    harness, meeting
) -> None:
    owner, editor, _, recording, ids = meeting
    url = f"/api/v1/speakers/recordings/{recording.public_id}/speaker-candidates"
    async with harness.client(owner) as client:
        mine = (await client.get(url)).json()
    async with harness.client(editor) as client:
        theirs = (await client.get(url)).json()
    assert {person["name"] for person in mine} == {"Alice", "Carol"}
    assert [person["name"] for person in theirs] == ["Alice"]
    assert set(theirs[0]) == {"id", "name", "color"}


@pytest.mark.anyio
async def test_shared_reads_leave_out_the_owners_contact_details(
    harness, meeting
) -> None:
    owner, editor, _, recording, _ = meeting
    url = f"/api/v1/recordings/{recording.public_id}"
    async with harness.client(owner) as client:
        own = (await client.get(url)).json()
    async with harness.client(editor) as client:
        shared = (await client.get(url)).json()

    def alice(read):
        return next(
            speaker["global_speaker"]
            for speaker in read["speakers"]
            if speaker["global_speaker"]
            and speaker["global_speaker"]["name"] == "Alice"
        )

    assert alice(own)["email"] == "alice@example.org"
    assert alice(own)["notes"] == "Prefers mornings"
    assert alice(shared)["email"] is None
    assert alice(shared)["phone_number"] is None
    assert alice(shared)["notes"] is None


@pytest.mark.anyio
async def test_editor_rename_links_the_owners_person_not_their_own(
    harness, meeting
) -> None:
    _, editor, _, recording, ids = meeting
    async with harness.session_maker() as session:
        # The editor keeps a Carol of their own; the meeting is the owner's.
        session.add(GlobalSpeaker(name="Carol", user_id=editor.id))
        await session.commit()
    async with harness.client(editor) as client:
        response = await client.put(
            f"/api/v1/speakers/recordings/{recording.public_id}",
            json={"diarization_label": "SPEAKER_01", "global_speaker_name": "Carol"},
        )
    assert response.status_code == 200, response.text
    async with harness.session_maker() as session:
        speaker = (
            await session.execute(
                select(RecordingSpeaker).where(
                    RecordingSpeaker.recording_id == recording.id,
                    RecordingSpeaker.diarization_label == "SPEAKER_01",
                )
            )
        ).scalar_one()
    assert speaker.global_speaker_id == ids["carol"]
