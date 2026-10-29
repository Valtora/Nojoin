"""Capture resume geometry survives asynchronous ingestion and runtime resets."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from backend.api.v1.endpoints.recordings import routes_capture as capture
from backend.models.recording import ClientStatus, RecordingStatus
from backend.models.transcript import Transcript
from backend.utils import reprocess_publication
from backend.utils.capture_resume import (
    RESUME_BOUNDARIES,
    RESUME_SEQUENCES,
    persist_resume_boundaries,
    resolve_resume_boundaries,
)
from backend.utils.transcript_settling import (
    durable_settling_state,
    resolve_capture_settling_policy,
    update_capture_settling_state,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_resume_marks_next_upload_once_and_refuses_stale_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = SimpleNamespace(
        id=1,
        public_id="capture",
        capture_epoch=3,
        status=RecordingStatus.PAUSED,
        client_status=ClientStatus.RECORDING,
        last_activity_at=None,
    )
    transcript = Transcript(recording_id=1)
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock(), add=lambda row: None)
    monkeypatch.setattr(
        capture.recordings_module,
        "_get_owned_recording",
        AsyncMock(return_value=recording),
    )
    monkeypatch.setattr(
        capture, "lock_capture_publication", AsyncMock(return_value=transcript)
    )
    monkeypatch.setattr(capture, "close_live_words", AsyncMock())
    monkeypatch.setattr(
        capture.recordings_module, "_reopen_recording_audio_cursor", AsyncMock()
    )
    monkeypatch.setattr(
        capture.recordings_module, "_get_last_uploaded_sequence", lambda _id: 4
    )
    for _ in range(2):
        response = await capture.resume_upload(
            "capture", epoch=3, db=db, current_user=SimpleNamespace(id=1)
        )
        assert response.last_sequence == 4
    assert transcript.settling_state == {RESUME_SEQUENCES: [5]}
    with pytest.raises(HTTPException) as refused:
        await capture.resume_upload(
            "capture", epoch=2, db=db, current_user=SimpleNamespace(id=1)
        )
    assert refused.value.status_code == 409
    assert transcript.settling_state == {RESUME_SEQUENCES: [5]}


def test_transcode_finishing_first_does_not_move_the_resume_cut() -> None:
    state = {RESUME_SEQUENCES: [5]}
    late = SimpleNamespace(sequence_no=6, absolute_start_ms=0)
    first = SimpleNamespace(sequence_no=5, absolute_start_ms=0)
    assert resolve_resume_boundaries(state, [late, first], through_sequence=4) == []
    first.absolute_start_ms = 10_250
    late.absolute_start_ms = 12_250
    assert resolve_resume_boundaries(state, [late, first], through_sequence=6) == [
        10_250
    ]


def test_final_offsets_outlive_chunks_and_runtime_reset() -> None:
    transcript = Transcript(recording_id=1, settling_state={RESUME_SEQUENCES: [3, 5]})
    chunks = [
        SimpleNamespace(sequence_no=4, absolute_start_ms=6500),
        SimpleNamespace(sequence_no=5, absolute_start_ms=8500),
    ]
    # A corrupt first resumed chunk has no audio; the next valid chunk begins there.
    persist_resume_boundaries(transcript, chunks)
    assert transcript.settling_state[RESUME_BOUNDARIES] == [6500, 8500]
    transcript.settling_state = durable_settling_state(transcript.settling_state)
    assert resolve_resume_boundaries(transcript.settling_state, []) == [6500, 8500]


@pytest.mark.parametrize("settled", [False, True])
def test_geometry_survives_both_reprocess_reset_paths(
    settled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {RESUME_BOUNDARIES: [4200], "eligible": True, "policy": {"enabled": True}}
    if settled:
        state["settled"] = True
    durable = durable_settling_state(state)
    assert durable[RESUME_BOUNDARIES] == [4200]
    assert "eligible" not in durable and "policy" not in durable
    transcript = Transcript(
        recording_id=1, settling_state={**state, "reprocess_pending": True}
    )
    monkeypatch.setattr(
        reprocess_publication, "_load_transcript", lambda *args, **kwargs: transcript
    )
    session = SimpleNamespace(execute=lambda statement: None, add=lambda row: None)
    reprocess_publication.clear_previous_reprocess_state(session, 1)
    assert transcript.settling_state == {
        RESUME_BOUNDARIES: [4200],
        "reprocess_pending": True,
        "reprocess_published": True,
    }


def test_resume_metadata_does_not_enable_settling_or_require_a_policy() -> None:
    transcript = Transcript(recording_id=1, settling_state={RESUME_SEQUENCES: [2]})
    update_capture_settling_state(
        transcript, capture_epoch=1, sealed=False, phase="live"
    )
    resolve_capture_settling_policy(transcript, device="cpu")
    assert transcript.settling_state == {RESUME_SEQUENCES: [2]}
