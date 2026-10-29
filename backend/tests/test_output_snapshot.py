"""Snapshot retries protect output evidence from live mutations."""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session

from backend.tests.test_meeting_analysis_task import _make_engine, _seed
from backend.utils import output_snapshot


def test_projection_identity_matches_live_analytics_without_reviving_retired_labels(
    tmp_path: Path,
) -> None:
    engine = _make_engine(tmp_path, "projection-identities")
    with engine.begin() as connection:
        _seed(connection)
        connection.execute(text("DELETE FROM transcript_utterances"))
        connection.execute(
            text(
                "UPDATE recording_speakers SET merged_into_id=1, speaker_status='merged' WHERE id=2"
            )
        )
        connection.execute(
            text("UPDATE transcripts SET segments=:segments"),
            {
                "segments": json.dumps(
                    [
                        {
                            "speaker": "SPEAKER_00",
                            "text": "Current",
                            "start": 0,
                            "end": 3,
                        },
                        {
                            "speaker": "SPEAKER_01",
                            "text": "Retired",
                            "start": 3,
                            "end": 6,
                        },
                    ]
                )
            },
        )
    with Session(engine) as session:
        result = output_snapshot.capture_output_snapshot(session, 10)
    assert result.segments[0]["recording_speaker_id"] == 1
    assert result.segments[1].get("recording_speaker_id") is None
    assert result.speaker_map["rs:1"] == "Priya Patel"
    assert "SPEAKER_01" not in result.speaker_map


def test_snapshot_retries_when_text_and_names_move_during_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _make_engine(tmp_path, "snapshot")
    with engine.begin() as connection:
        _seed(connection)
    original = output_snapshot.build_transcript_segments_for_read
    reads = 0

    def moving_read(session: Session, recording_id: int) -> list[dict[str, Any]]:
        nonlocal reads
        segments = original(session, recording_id)
        reads += 1
        if reads == 1:
            # Commit between reading the words and reading their names, exactly
            # where READ COMMITTED previously mixed two transcript versions.
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE transcript_utterances SET text='New words' WHERE id=1")
                )
                connection.execute(
                    text(
                        "UPDATE recording_speakers SET local_name='New name' WHERE id=1"
                    )
                )
                connection.execute(
                    text("UPDATE transcripts SET change_sequence=change_sequence+1")
                )
                connection.execute(
                    text("UPDATE transcript_utterance_events SET id=10 WHERE id=9")
                )
        return segments

    monkeypatch.setattr(
        output_snapshot, "build_transcript_segments_for_read", moving_read
    )
    with Session(engine) as session:
        result = output_snapshot.capture_output_snapshot(session, 10)
    assert reads == 2
    assert result.segments[0]["text"] == "New words"
    assert result.speaker_map["SPEAKER_00"] == "New name"
    assert result.event_watermark == 10
    assert result.generated_through_ms == 18000


def test_continuously_moving_snapshot_is_retryable_instead_of_mislabeled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _make_engine(tmp_path, "moving")
    with engine.begin() as connection:
        _seed(connection)
    original = output_snapshot.build_transcript_segments_for_read

    def moving_read(session: Session, recording_id: int) -> list[dict[str, Any]]:
        segments = original(session, recording_id)
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE transcripts SET change_sequence=change_sequence+1")
            )
        return segments

    monkeypatch.setattr(
        output_snapshot, "build_transcript_segments_for_read", moving_read
    )
    with Session(engine) as session, pytest.raises(RuntimeError, match="Please retry"):
        output_snapshot.capture_output_snapshot(session, 10)
