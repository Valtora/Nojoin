"""What the speaker API reports about who last named a speaker."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.models.recording_public import serialize_recording_speaker
from backend.models.speaker import RecordingSpeaker


@pytest.mark.parametrize("source", ["mcp", "api", None])
def test_the_speaker_says_where_its_name_was_last_edited(source) -> None:
    # The panel's "AI named" badge reads this; without it the badge never shows.
    now = datetime.now(UTC).replace(tzinfo=None)
    speaker = RecordingSpeaker(
        id=3,
        created_at=now,
        updated_at=now,
        recording_id=1,
        diarization_label="SPEAKER_00",
        local_name="Dana",
        name_last_edit_source=source,
    )

    read = serialize_recording_speaker(speaker, recording_public_id="rec-1")

    assert read.name_last_edit_source == source
