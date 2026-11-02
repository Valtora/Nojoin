"""Live names retain their authority when finalisation replaces their labels."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import select

from backend.models.pipeline import TranscriptUtterance
from backend.models.speaker import RecordingSpeaker
from backend.tests.live_naming_fixtures import THREE, TWO
from backend.tests.test_live_speaker_assignment_postgres import live_api as live_api
from backend.utils.canonical_pipeline import finalize_utterances_from_segments
from backend.worker.tasks import speaker_assignment


@pytest.mark.anyio
@pytest.mark.parametrize("correction", ["none", "unrelated", "partial", "all"])
async def test_live_name_reprocessing_distinguishes_replaced_and_reassigned_speech(
    live_api: tuple[Any, ...], correction: str
) -> None:
    client, sessions, recording = live_api
    response = await client.put(
        f"/api/v1/speakers/recordings/{recording.public_id}/live/{TWO}",
        json={"name": "Alex"},
    )
    assert response.status_code == 200, response.text
    url = f"/api/v1/transcripts/{recording.public_id}/utterances"
    lines = (await client.get(url)).json()["utterances"]
    indices = {"none": [], "unrelated": [0], "partial": [2], "all": [1, 2]}
    for index in indices[correction]:
        line = lines[index]
        changed = await client.patch(
            f"{url}/{line['id']}/speaker",
            json={
                "new_speaker_name": "Speaker 3",
                "diarization_label": THREE,
                "scope": "utterance_only",
                "expected_revision": line["revision"],
            },
        )
        assert changed.status_code == 200, changed.text
    final = [
        {"start": 0.0, "end": 10.0, "speaker": "SPEAKER_00", "text": "First"},
        {"start": 12.0, "end": 14.0, "speaker": "SPEAKER_01", "text": "Alex"},
        {"start": 15.0, "end": 17.0, "speaker": "SPEAKER_01", "text": "Alex again"},
        {"start": 20.0, "end": 24.0, "speaker": "SPEAKER_02", "text": "Third"},
    ]
    async with sessions() as db:

        def finish(sync: Any) -> None:
            current = sync.get(type(recording), recording.id)
            ctx = SimpleNamespace(
                session=sync, merged_config={}, recording_id=recording.id
            )
            speaker_assignment.assign_and_identify_speakers(ctx, current, final, None)
            finalize_utterances_from_segments(
                sync, recording_id=recording.id, segments=final
            )
            sync.flush()
            rows = sync.exec(select(TranscriptUtterance)).all()
            assert not any(row.speaker_label == TWO for row in rows)
            if correction == "none":
                assert not any(row.manual_speaker_locked for row in rows)
            relabel = {
                "SPEAKER_00": "SPEAKER_03",
                "SPEAKER_01": "SPEAKER_04",
                "SPEAKER_02": "SPEAKER_05",
            }
            reprocessed = [{**row, "speaker": relabel[row["speaker"]]} for row in final]
            speaker_assignment.assign_and_identify_speakers(
                ctx, current, reprocessed, None
            )
            target = sync.exec(
                select(RecordingSpeaker).where(
                    RecordingSpeaker.recording_id == recording.id,
                    RecordingSpeaker.diarization_label == "SPEAKER_04",
                )
            ).one()
            assert target.local_name == "Alex"
            assert target.identity_locked is (correction != "all")

        await db.run_sync(finish)
