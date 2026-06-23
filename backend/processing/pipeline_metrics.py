from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

logger = logging.getLogger(__name__)

PIPELINE_METRIC_PREFIX = "pipeline_metric"
METRICS_JSONL_ENV = "NOJOIN_PIPELINE_METRICS_JSONL"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return str(value)


def build_pipeline_metric(
    *,
    stage: str,
    recording_id: int | str | None = None,
    payload: dict[str, Any] | None = None,
    status: str = "ok",
    elapsed_ms: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = now or datetime.now(UTC)
    event: dict[str, Any] = {
        "timestamp": timestamp.isoformat(),
        "stage": stage,
        "status": status,
        "recording_id": recording_id,
        "payload": _json_safe(payload or {}),
    }
    if elapsed_ms is not None:
        event["elapsed_ms"] = round(float(elapsed_ms), 3)
    return event


def record_pipeline_metric(
    *,
    stage: str,
    recording_id: int | str | None = None,
    payload: dict[str, Any] | None = None,
    status: str = "ok",
    elapsed_ms: float | None = None,
    log: logging.Logger | None = None,
) -> dict[str, Any]:
    event = build_pipeline_metric(
        stage=stage,
        recording_id=recording_id,
        payload=payload,
        status=status,
        elapsed_ms=elapsed_ms,
    )
    line = json.dumps(event, sort_keys=True, separators=(",", ":"))
    (log or logger).info("%s %s", PIPELINE_METRIC_PREFIX, line)

    jsonl_path = os.getenv(METRICS_JSONL_ENV)
    if jsonl_path:
        path = Path(jsonl_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    return event


@contextmanager
def pipeline_metric_timer(
    *,
    stage: str,
    recording_id: int | str | None = None,
    payload: dict[str, Any] | None = None,
    log: logging.Logger | None = None,
) -> Iterator[dict[str, Any]]:
    start = time.perf_counter()
    context: dict[str, Any] = {"payload": dict(payload or {})}
    try:
        yield context
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        error_payload = dict(context.get("payload") or {})
        error_payload["error"] = str(exc)
        record_pipeline_metric(
            stage=stage,
            recording_id=recording_id,
            payload=error_payload,
            status="error",
            elapsed_ms=elapsed_ms,
            log=log,
        )
        raise
    else:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        record_pipeline_metric(
            stage=stage,
            recording_id=recording_id,
            payload=context.get("payload") or {},
            elapsed_ms=elapsed_ms,
            log=log,
        )


@contextmanager
def rolling_diarization_window_timer(
    *,
    recording_id: int | str | None,
    window_start_s: float,
    window_end_s: float,
    window_index: int | None = None,
    model: str | None = None,
    device: str | None = None,
    config_hash: str | None = None,
    payload: dict[str, Any] | None = None,
    log: logging.Logger | None = None,
) -> Iterator[dict[str, Any]]:
    metric_payload: dict[str, Any] = {
        "window_start_s": round(float(window_start_s), 3),
        "window_end_s": round(float(window_end_s), 3),
        "window_duration_s": round(float(window_end_s) - float(window_start_s), 3),
    }
    if window_index is not None:
        metric_payload["window_index"] = window_index
    if model:
        metric_payload["model"] = model
    if device:
        metric_payload["device"] = device
    if config_hash:
        metric_payload["config_hash"] = config_hash
    if payload:
        metric_payload.update(payload)

    with pipeline_metric_timer(
        stage="rolling_diarization_window",
        recording_id=recording_id,
        payload=metric_payload,
        log=log,
    ) as metric:
        yield metric


def load_pipeline_metrics_jsonl(path: str | Path) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                metrics.append(json.loads(stripped))
    return metrics


def summarize_pipeline_metrics(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    events_list = list(events)
    stage_counts = Counter(str(event.get("stage")) for event in events_list)
    status_counts = Counter(str(event.get("status", "ok")) for event in events_list)
    speaker_match_kinds = Counter(
        str((event.get("payload") or {}).get("match_kind"))
        for event in events_list
        if event.get("stage") == "live_speaker_resolved"
    )
    speaker_match_kinds.pop("None", None)

    live_segments_emitted = sum(
        int((event.get("payload") or {}).get("segment_count", 0) or 0)
        for event in events_list
        if event.get("stage") == "live_segments_persisted"
    )
    finalization_ms = sum(
        float(event.get("elapsed_ms", 0.0) or 0.0)
        for event in events_list
        if event.get("stage") == "final_processing_completed"
    )
    manual_speaker_edits = sum(
        int((event.get("payload") or {}).get("manual_speaker_edits", 0) or 0)
        for event in events_list
        if event.get("stage") == "final_live_reconciliation"
    )
    preserved_manual_speaker_edits = sum(
        int((event.get("payload") or {}).get("preserved_manual_speaker_edits", 0) or 0)
        for event in events_list
        if event.get("stage") == "final_live_reconciliation"
    )
    manual_text_edits = sum(
        int((event.get("payload") or {}).get("manual_text_edits", 0) or 0)
        for event in events_list
        if event.get("stage") == "final_live_reconciliation"
    )
    preserved_manual_text_edits = sum(
        int((event.get("payload") or {}).get("preserved_manual_text_edits", 0) or 0)
        for event in events_list
        if event.get("stage") == "final_live_reconciliation"
    )

    speaker_preservation_rate = None
    if manual_speaker_edits:
        speaker_preservation_rate = (
            preserved_manual_speaker_edits / manual_speaker_edits
        )
    text_preservation_rate = None
    if manual_text_edits:
        text_preservation_rate = preserved_manual_text_edits / manual_text_edits

    return {
        "event_count": len(events_list),
        "stage_counts": dict(stage_counts),
        "status_counts": dict(status_counts),
        "asr_invocations": stage_counts.get("live_asr_region", 0)
        + stage_counts.get("final_asr_invocation", 0),
        "diarization_invocations": stage_counts.get("final_diarization_invocation", 0)
        + stage_counts.get("rolling_diarization_window", 0),
        "live_segments_emitted": live_segments_emitted,
        "live_transcript_reuse_count": stage_counts.get(
            "final_transcription_reused_live", 0
        ),
        "final_asr_rerun_count": stage_counts.get("final_asr_invocation", 0),
        "speaker_match_kinds": dict(speaker_match_kinds),
        "speaker_correction_events": stage_counts.get("speaker_correction_applied", 0),
        "text_correction_events": stage_counts.get(
            "transcript_text_correction_applied", 0
        ),
        "manual_speaker_edit_preservation_rate": speaker_preservation_rate,
        "manual_text_edit_preservation_rate": text_preservation_rate,
        "finalization_ms": round(finalization_ms, 3),
    }


def score_turn_overlap_proxy(
    reference_turns: Iterable[dict[str, Any]],
    predicted_turns: Iterable[dict[str, Any]],
) -> dict[str, float]:
    reference = list(reference_turns)
    predicted = list(predicted_turns)
    total_reference_s = sum(
        max(0.0, float(turn.get("end", 0.0)) - float(turn.get("start", 0.0)))
        for turn in reference
    )
    if total_reference_s <= 0.0:
        return {"total_reference_s": 0.0, "matched_s": 0.0, "overlap_score": 0.0}

    matched_s = 0.0
    for ref_turn in reference:
        ref_start = float(ref_turn.get("start", 0.0))
        ref_end = float(ref_turn.get("end", 0.0))
        ref_speaker = str(ref_turn.get("speaker", ""))
        for predicted_turn in predicted:
            if str(predicted_turn.get("speaker", "")) != ref_speaker:
                continue
            overlap_start = max(ref_start, float(predicted_turn.get("start", 0.0)))
            overlap_end = min(ref_end, float(predicted_turn.get("end", 0.0)))
            matched_s += max(0.0, overlap_end - overlap_start)

    return {
        "total_reference_s": round(total_reference_s, 3),
        "matched_s": round(matched_s, 3),
        "overlap_score": round(min(1.0, matched_s / total_reference_s), 4),
    }


def write_pipeline_baseline_report(summary: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Pipeline Baseline Summary",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "## Metrics",
        "",
        f"- Event count: {summary.get('event_count', 0)}",
        f"- ASR invocations: {summary.get('asr_invocations', 0)}",
        f"- Diarization invocations: {summary.get('diarization_invocations', 0)}",
        f"- Live segments emitted: {summary.get('live_segments_emitted', 0)}",
        f"- Live transcript reuse count: {summary.get('live_transcript_reuse_count', 0)}",
        f"- Final ASR rerun count: {summary.get('final_asr_rerun_count', 0)}",
        f"- Speaker correction events: {summary.get('speaker_correction_events', 0)}",
        f"- Text correction events: {summary.get('text_correction_events', 0)}",
        f"- Manual speaker edit preservation rate: {summary.get('manual_speaker_edit_preservation_rate')}",
        f"- Manual text edit preservation rate: {summary.get('manual_text_edit_preservation_rate')}",
        f"- Finalization ms: {summary.get('finalization_ms', 0.0)}",
        "",
        "## Stage Counts",
        "",
    ]
    for stage, count in sorted((summary.get("stage_counts") or {}).items()):
        lines.append(f"- `{stage}`: {count}")
    lines.extend(["", "## Speaker Match Kinds", ""])
    for match_kind, count in sorted((summary.get("speaker_match_kinds") or {}).items()):
        lines.append(f"- `{match_kind}`: {count}")
    lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
