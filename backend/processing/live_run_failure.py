"""Recovery for a live-lane run that failed.

Moved out of ``live_transcribe`` (which imports it lazily, from the task) to keep
that module under its grandfathered size. Helpers of the live lane are reached
through the module (``lt.``), so tests that patch them there still apply.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.processing import live_transcribe as lt
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.asr_window_results import fail_recording_asr_window_result
from backend.utils.config_manager import config_manager

logger = lt.logger


@dataclass
class LiveRun:
    """One drain of the live lane."""

    recording_id: int
    sequence: int
    run: list[int]
    live_dir: Path
    buffer_path: str
    context_path: str


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


def record_live_run_failure(
    live_run: LiveRun,
    exc: Exception,
    state: dict,
    live_config: dict | None,
    pending_asr_completions: list[dict[str, Any]] | None,
) -> None:
    """Best-effort recovery for a failed live run; never raises.

    Non-fatal by contract: records the failure metric, marks pending ASR ledger
    rows failed, marks the drained sequences ``failed`` and advances
    next_expected so the lane keeps moving. The source windows stay discoverable
    via their pending ASR coverage so the final pipeline recovers them.
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
    state["next_expected"] = run[-1] + 1
    lt._write_live_state_best_effort(live_run.live_dir, state)
    lt._record_live_sequence_outcome_metric(
        recording_id=recording_id,
        sequence=sequence,
        outcome="failed",
        reason="live_run_failed",
        run=run,
        extra_payload={
            "error": str(exc),
            "next_expected": state["next_expected"],
            "catch_up_recoverable": True,
        },
    )
