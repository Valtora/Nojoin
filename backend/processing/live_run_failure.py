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
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from backend.processing import live_transcribe as lt
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.asr_window_results import fail_recording_asr_window_result
from backend.utils.config_manager import config_manager

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


def _fail_pending_asr_results(
    live_run: LiveRun,
    exc: Exception,
    live_config: dict | None,
    pending_asr_completions: list[dict[str, Any]],
) -> None:
    run = live_run.run
    # Bind error details to plain locals: `exc` is unbound once the caller's
    # except block exits and the callbacks below run best-effort after.
    persistence_error_summary = (
        str(exc).strip()[:500] or "Live utterance persistence failed."
    )
    persistence_error_type = exc.__class__.__name__
    for pending_result in pending_asr_completions:
        lt._persist_asr_window_result_best_effort(
            lambda ledger_session, pending_result=pending_result: (
                fail_recording_asr_window_result(
                    ledger_session,
                    recording_id=live_run.recording_id,
                    source_kind="live",
                    span_start_ms=pending_result["span_start_ms"],
                    span_end_ms=pending_result["span_end_ms"],
                    chunk_start_sequence=run[0] if run else None,
                    chunk_end_sequence=run[-1] if run else None,
                    config=live_config,
                    error_summary=persistence_error_summary,
                    error_payload={"error_type": persistence_error_type},
                )
            )
        )


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
        logger.error(
            "Could not save the live state after failed run %s of recording %s",
            run,
            live_run.recording_id,
            exc_info=True,
        )
        return
    if spent:
        _remove_spent_audio(live_run)


def record_live_run_failure(
    live_run: LiveRun,
    exc: Exception,
    state: dict,
    live_config: dict | None,
    pending_asr_completions: list[dict[str, Any]] | None,
) -> None:
    """Best-effort recovery for a failed live run; never raises.

    Non-fatal by contract: records the failure metric, marks pending ASR ledger
    rows failed, marks the drained sequences ``failed``, advances next_expected
    so the lane keeps moving, and moves the timeline past the run's audio. The
    source windows stay discoverable via their pending ASR coverage so the final
    pipeline recovers them.
    """
    recording_id, sequence, run = live_run.recording_id, live_run.sequence, live_run.run
    record_pipeline_metric(
        stage="live_run_failed",
        recording_id=recording_id,
        payload={
            "sequence": sequence,
            "run": run,
            "error": str(exc),
            "catch_up_recoverable": bool(run),
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
    if pending_asr_completions is not None and config_manager.get(
        "enable_asr_window_result_ledger", True
    ):
        _fail_pending_asr_results(live_run, exc, live_config, pending_asr_completions)
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
            "catch_up_recoverable": True,
        },
    )
