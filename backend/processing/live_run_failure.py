"""Recovery for a live-lane run that failed.

A live run reads the carried buffer (``live/buffer.wav``) followed by the chunks
it drained, and that audio starts at ``buffer_abs_start`` on the recording
timeline. A successful run keeps the audio past its cut point as the next buffer
and moves ``buffer_abs_start`` to where that buffer starts. A failed run has to
account for its audio as well. Advancing only ``next_expected`` leaves the old
buffer and start time in place: the next run transcribes the stale buffer again
and stamps every later utterance early by the length of the failed run.

Moved out of ``live_transcribe`` (which imports it lazily, from the task) to keep
that module under its grandfathered size. Helpers of the live lane are reached
through the module (``lt.``), so tests that patch them there still apply.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from backend.processing import live_transcribe as lt
from backend.processing.pipeline_metrics import record_pipeline_metric

logger = lt.logger


@dataclass
class LiveRun:
    """One drain of the live lane, and how far it got.

    Drained chunks sit in ``temp_dir`` as ``{sequence}.wav``; ``live_dir`` holds
    the lane's state, the carried buffer (``buffer_path``) and the left-context
    run-up (``context_path``). ``combined_len`` is the seconds of audio the run
    combined (carried buffer plus drained chunks); it stays None when the run
    failed while reading that audio. ``carried_abs_start`` is set once the run
    has written the audio past its cut point as the next buffer, and is where
    that buffer starts.
    """

    recording_id: int
    sequence: int
    run: list[int]
    temp_dir: Path
    live_dir: Path
    buffer_path: str
    context_path: str
    combined_len: float | None = None
    carried_abs_start: float | None = None


def _wav_duration_s(path: str) -> float | None:
    import soundfile

    try:
        return float(soundfile.info(path).duration)
    except (OSError, RuntimeError) as exc:
        logger.warning("Could not read the duration of %s: %s", path, exc)
        return None


def _recorded_chunk_durations_ms(recording_id: int) -> dict[int, int]:
    """Durations the upload path recorded for each chunk, by sequence number."""
    from backend.core.db import get_sync_session

    session = get_sync_session()
    try:
        chunks = lt._load_recording_audio_chunks(session, recording_id)
    finally:
        session.close()
    return {int(chunk.sequence_no): int(chunk.duration_ms) for chunk in chunks}


def _unread_run_length_s(
    live_run: LiveRun, state: dict, chunk_durations_ms: dict[int, int]
) -> float:
    """Seconds of audio in a run that failed before it combined its audio.

    Read from the WAV headers. For a header that cannot be read, the carried
    buffer falls back to the length the lane saved with it, and a chunk to the
    duration the upload recorded.
    """
    total = 0.0
    if os.path.exists(live_run.buffer_path):
        seconds = _wav_duration_s(live_run.buffer_path)
        if seconds is None:
            seconds = state.get("buffer_len_s")
        if seconds is None:
            logger.warning("No length for the carried live buffer; counting 0 s.")
        total += seconds or 0.0
    for sequence in live_run.run:
        seconds = _wav_duration_s(str(live_run.temp_dir / f"{sequence}.wav"))
        if seconds is None and sequence in chunk_durations_ms:
            seconds = chunk_durations_ms[sequence] / 1000.0
        if seconds is None:
            logger.warning("No duration for live chunk %s; counting 0 s.", sequence)
        total += seconds or 0.0
    return total


def end_of_failed_run(
    live_run: LiveRun, state: dict, chunk_durations_ms: dict[int, int]
) -> tuple[float, float]:
    """Return ``(buffer_abs_start, buffer_len_s)`` for the run after a failed one.

    A run that failed after carrying its tail into the next buffer keeps that
    buffer, which starts where the carry-over says. Otherwise every second the
    run read is spent and the next run starts after it with no buffer.
    """
    start = float(state["buffer_abs_start"])
    if live_run.carried_abs_start is not None and live_run.combined_len is not None:
        carried_len = start + live_run.combined_len - live_run.carried_abs_start
        return live_run.carried_abs_start, carried_len
    combined_len = live_run.combined_len
    if combined_len is None:
        combined_len = _unread_run_length_s(live_run, state, chunk_durations_ms)
    return start + combined_len, 0.0


def _next_timeline(live_run: LiveRun, state: dict) -> tuple[float, float]:
    chunk_durations_ms = (
        {}
        if live_run.combined_len is not None
        else _recorded_chunk_durations_ms(live_run.recording_id)
    )
    return end_of_failed_run(live_run, state, chunk_durations_ms)


def capture_discarded(recording_id: int) -> bool:
    """Whether the recording is gone, as after a Discard during the run.

    Discard deletes the row and then removes the capture's temporary folder, so
    a run in flight fails to write its clip, carry or state. That failure is
    expected. An error reading the database counts as the recording being there,
    so a real failure is still reported.
    """
    from backend.core.db import get_sync_session
    from backend.models.recording import Recording

    session = get_sync_session()
    try:
        return session.get(Recording, recording_id) is None
    except SQLAlchemyError:
        return False
    finally:
        session.close()


def _stop_for_discard(live_run: LiveRun) -> None:
    logger.info(
        "Live run %s of recording %s stopped: the recording was discarded",
        live_run.run,
        live_run.recording_id,
    )
    lt._record_live_sequence_outcome_metric(
        recording_id=live_run.recording_id,
        sequence=live_run.sequence,
        outcome="skipped",
        reason="recording_discarded",
        run=live_run.run,
    )


def _remove_spent_audio(live_run: LiveRun) -> None:
    """Remove the spent buffer and the run-up that no longer precedes anything."""
    for path in (live_run.buffer_path, live_run.context_path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            # The saved state says this audio is spent; if the buffer survives,
            # the next run will replay it.
            logger.error("Could not remove spent live audio %s: %s", path, exc)


def _save_advance(live_run: LiveRun, state: dict) -> None:
    """Advance ``next_expected`` and the timeline past the run, then save them.

    The new state is written before any audio is removed, in one write. If the
    timeline cannot be worked out, ``next_expected`` still advances. If the
    write fails, the spent audio stays where it is, consistent with the state
    still on disk.
    """
    run = live_run.run
    state["next_expected"] = run[-1] + 1
    spent = False
    try:
        state["buffer_abs_start"], state["buffer_len_s"] = _next_timeline(
            live_run, state
        )
        spent = live_run.carried_abs_start is None
    except (OSError, RuntimeError, ValueError, SQLAlchemyError) as timeline_exc:
        logger.warning(
            "Could not move the live timeline past failed run %s of recording %s: %s",
            run,
            live_run.recording_id,
            timeline_exc,
        )
    try:
        lt.write_live_state(live_run.live_dir, state)
    except OSError:
        if capture_discarded(live_run.recording_id):
            _stop_for_discard(live_run)
            return
        logger.error(
            "Could not save the live state after failed run %s of recording %s",
            run,
            live_run.recording_id,
            exc_info=True,
        )
        return
    if spent:
        _remove_spent_audio(live_run)


def record_live_run_failure(live_run: LiveRun, exc: Exception, state: dict) -> None:
    """Best-effort recovery for a failed live run; never raises.

    Non-fatal by contract: records the failure metric, marks the drained
    sequences ``failed``, advances next_expected so the lane keeps moving, and
    moves the timeline past the run's audio. Finalize transcribes the whole
    recording, so the run's audio still reaches the final transcript. A run
    whose recording was discarded under it stops at INFO and saves nothing.
    """
    recording_id, sequence, run = live_run.recording_id, live_run.sequence, live_run.run
    if capture_discarded(recording_id):
        _stop_for_discard(live_run)
        return
    record_pipeline_metric(
        stage="live_run_failed",
        recording_id=recording_id,
        payload={
            "sequence": sequence,
            "run": run,
            "error": str(exc),
        },
        status="error",
        log=logger,
    )
    logger.error(
        "Live transcription failed for recording %s run %s: %s",
        recording_id,
        run,
        exc,
        exc_info=True,
    )
    if not run:
        return
    for failed_sequence in run:
        lt._record_live_sequence_outcome(
            state,
            sequence=failed_sequence,
            outcome="failed",
            reason="live_run_failed",
            run=run,
            error=str(exc),
        )
    _save_advance(live_run, state)
    lt._record_live_sequence_outcome_metric(
        recording_id=recording_id,
        sequence=sequence,
        outcome="failed",
        reason="live_run_failed",
        run=run,
        extra_payload={
            "error": str(exc),
            "next_expected": state["next_expected"],
            "buffer_abs_start": state["buffer_abs_start"],
        },
    )
