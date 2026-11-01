"""A resume's line break goes where the tab's new audio starts (issue #815).

A tab can still hold audio from before the pause when it resumes: after an
upload stall, a pause sent offline, a failed Stop or a reload. That audio
uploads after the Resume, so a break stored after the server's last segment
lands where the queued audio starts. The holder says where its new audio
starts on the resume; a reloaded tab, which learns it only from the answer,
moves the break with its first new segment.
"""

import asyncio
from pathlib import Path
from typing import Any

from httpx import AsyncClient

from backend.models.transcript import Transcript
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.tests.test_capture_resume_lifecycle import (
    _capture_client,
    _settling_state,
    _wav,
)
from backend.tests.test_capture_stop_publication import CaptureHarness
from backend.tests.test_capture_stop_publication import (
    capture_harness as capture_harness,
)
from backend.utils.capture_resume import RESUME_SEQUENCES, move_capture_resume


async def _segment(
    client: AsyncClient,
    base: str,
    directory: Path,
    sequence: int,
    epoch: int,
    **params: Any,
) -> int:
    _wav(directory.parent / "upload.wav", 250)
    response = await client.post(
        f"{base}/segment",
        params={"sequence": sequence, "epoch": epoch, **params},
        files={
            "file": (
                f"{sequence}.wav",
                (directory.parent / "upload.wav").read_bytes(),
                "audio/wav",
            )
        },
    )
    return response.status_code


def test_a_holder_with_queued_audio_breaks_where_its_new_audio_starts(
    capture_harness: CaptureHarness,
) -> None:
    """An upload stall paused the tab with 2 and 3 still queued; new audio is 4."""

    async def run() -> None:
        engine, _, public_id, _, directory = capture_harness
        base = f"/api/v1/recordings/{public_id}"
        async with _capture_client(capture_harness) as client:
            for sequence in (0, 1):
                assert await _segment(client, base, directory, sequence, 0) == 200
            assert (await client.post(f"{base}/pause", params={"epoch": 0})).is_success
            resumed = await client.post(
                f"{base}/resume", params={"epoch": 0, "next_sequence": 4}
            )
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["last_sequence"] == 1
            assert _settling_state(engine)[RESUME_SEQUENCES] == [4]
            for sequence in (2, 3, 4):
                assert await _segment(client, base, directory, sequence, 0) == 200
            stopped = await client.post(f"{base}/finalize", params={"epoch": 0})
            assert stopped.status_code == 200, stopped.text
        # Five 250 ms segments: the resumed audio starts 1 s in, not 500 ms.
        assert _settling_state(engine)["resume_boundaries_ms"] == [1000]

    asyncio.run(run())


def test_the_server_keeps_its_own_break_when_the_tab_says_earlier(
    capture_harness: CaptureHarness,
) -> None:

    async def run() -> None:
        engine, _, public_id, _, directory = capture_harness
        base = f"/api/v1/recordings/{public_id}"
        async with _capture_client(capture_harness) as client:
            for sequence in (0, 1):
                assert await _segment(client, base, directory, sequence, 0) == 200
            assert (await client.post(f"{base}/pause", params={"epoch": 0})).is_success
            resumed = await client.post(
                f"{base}/resume", params={"epoch": 0, "next_sequence": 1}
            )
            assert resumed.status_code == 200, resumed.text
        assert _settling_state(engine)[RESUME_SEQUENCES] == [2]

    asyncio.run(run())


def test_a_reloaded_tab_moves_the_break_with_its_first_new_segment(
    capture_harness: CaptureHarness,
) -> None:
    """The reloaded tab restores 2 and 3 and records new audio from 4."""

    async def run() -> None:
        engine, _, public_id, _, directory = capture_harness
        base = f"/api/v1/recordings/{public_id}"
        async with _capture_client(capture_harness) as client:
            for sequence in (0, 1):
                assert await _segment(client, base, directory, sequence, 0) == 200
            assert (await client.post(f"{base}/pause", params={"epoch": 0})).is_success
            resumed = await client.post(f"{base}/resume", params={"reclaim_epoch": 0})
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["capture_epoch"] == 1
            assert _settling_state(engine)[RESUME_SEQUENCES] == [2]
            for sequence in (2, 3):
                assert await _segment(client, base, directory, sequence, 1) == 200
            assert _settling_state(engine)[RESUME_SEQUENCES] == [2]
            # The old page's late upload cannot move it: its epoch is refused.
            assert await _segment(client, base, directory, 4, 0, resume_mark=2) == 409
            assert _settling_state(engine)[RESUME_SEQUENCES] == [2]
            assert await _segment(client, base, directory, 4, 1, resume_mark=2) == 200
            assert _settling_state(engine)[RESUME_SEQUENCES] == [4]
            # Sent again after a lost answer, it changes nothing.
            assert await _segment(client, base, directory, 4, 1, resume_mark=2) == 200
            assert await _segment(client, base, directory, 5, 1) == 200
            assert _settling_state(engine)[RESUME_SEQUENCES] == [4]
            stopped = await client.post(f"{base}/finalize", params={"epoch": 1})
            assert stopped.status_code == 200, stopped.text
        assert _settling_state(engine)["resume_boundaries_ms"] == [1000]

    asyncio.run(run())


def test_moving_a_break_keeps_the_other_resumes() -> None:
    transcript = Transcript(recording_id=1, settling_state={RESUME_SEQUENCES: [3, 9]})

    move_capture_resume(transcript, 9, 12)
    assert transcript.settling_state[RESUME_SEQUENCES] == [3, 12]

    # A break the tab says is later than its first new segment is not moved.
    move_capture_resume(transcript, 12, 10)
    assert transcript.settling_state[RESUME_SEQUENCES] == [3, 12]

    # A resume that stored no break (nothing uploaded yet) gains one.
    empty = Transcript(recording_id=2, settling_state=None)
    move_capture_resume(empty, 0, 2)
    assert empty.settling_state == {RESUME_SEQUENCES: [2]}

    move_capture_resume(None, 0, 2)
