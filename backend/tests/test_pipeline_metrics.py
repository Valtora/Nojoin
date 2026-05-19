import json
import logging
from pathlib import Path

import pytest

from backend.processing.pipeline_metrics import (
    METRICS_JSONL_ENV,
    build_pipeline_metric,
    load_pipeline_metrics_jsonl,
    pipeline_metric_timer,
    record_pipeline_metric,
    rolling_diarization_window_timer,
    score_turn_overlap_proxy,
    summarize_pipeline_metrics,
    write_pipeline_baseline_report,
)


def test_build_pipeline_metric_normalizes_payload_values():
    event = build_pipeline_metric(
        stage="live_asr_region",
        recording_id=42,
        payload={"path": Path("clip.wav"), "items": {"a", "b"}},
        elapsed_ms=12.34567,
    )

    assert event["stage"] == "live_asr_region"
    assert event["recording_id"] == 42
    assert event["elapsed_ms"] == 12.346
    assert event["payload"]["path"] == "clip.wav"
    assert sorted(event["payload"]["items"]) == ["a", "b"]


def test_record_pipeline_metric_logs_and_optionally_writes_jsonl(
    caplog,
    monkeypatch,
    tmp_path,
):
    jsonl_path = tmp_path / "metrics.jsonl"
    monkeypatch.setenv(METRICS_JSONL_ENV, str(jsonl_path))
    caplog.set_level(logging.INFO, logger="backend.processing.pipeline_metrics")

    event = record_pipeline_metric(
        stage="final_transcription_reused_live",
        recording_id="rec_1",
        payload={"segment_count": 3},
    )

    assert event["payload"] == {"segment_count": 3}
    assert jsonl_path.exists()
    loaded = load_pipeline_metrics_jsonl(jsonl_path)
    assert loaded == [event]
    assert any("pipeline_metric" in record.message for record in caplog.records)


def test_pipeline_metric_timer_records_success_and_errors(monkeypatch):
    events = []

    def capture_metric(**kwargs):
        events.append(kwargs)
        return kwargs

    monkeypatch.setattr(
        "backend.processing.pipeline_metrics.record_pipeline_metric",
        capture_metric,
    )

    with pipeline_metric_timer(stage="live_asr_region", recording_id=7) as metric:
        metric["payload"]["text_chars"] = 5

    with pytest.raises(RuntimeError):
        with pipeline_metric_timer(stage="final_diarization_invocation", recording_id=7):
            raise RuntimeError("missing token")

    assert events[0]["stage"] == "live_asr_region"
    assert events[0]["payload"] == {"text_chars": 5}
    assert events[1]["stage"] == "final_diarization_invocation"
    assert events[1]["status"] == "error"
    assert events[1]["payload"]["error"] == "missing token"


def test_summarize_pipeline_metrics_tracks_phase_zero_counts():
    summary = summarize_pipeline_metrics(
        [
            {"stage": "live_asr_region", "status": "ok", "payload": {}},
            {"stage": "final_asr_invocation", "status": "ok", "payload": {}},
            {"stage": "final_diarization_invocation", "status": "ok", "payload": {}},
            {"stage": "rolling_diarization_window", "status": "ok", "payload": {}},
            {
                "stage": "live_segments_persisted",
                "status": "ok",
                "payload": {"segment_count": 2},
            },
            {
                "stage": "live_speaker_resolved",
                "status": "ok",
                "payload": {"match_kind": "fallback_last_label"},
            },
            {
                "stage": "final_processing_completed",
                "status": "ok",
                "elapsed_ms": 123.456,
                "payload": {},
            },
        ]
    )

    assert summary["asr_invocations"] == 2
    assert summary["diarization_invocations"] == 2
    assert summary["live_segments_emitted"] == 2
    assert summary["speaker_match_kinds"] == {"fallback_last_label": 1}
    assert summary["finalization_ms"] == 123.456


def test_rolling_diarization_window_timer_records_window_metadata(monkeypatch):
    events = []

    def capture_metric(**kwargs):
        events.append(kwargs)
        return kwargs

    monkeypatch.setattr(
        "backend.processing.pipeline_metrics.record_pipeline_metric",
        capture_metric,
    )

    with rolling_diarization_window_timer(
        recording_id=99,
        window_start_s=10,
        window_end_s=35,
        window_index=2,
        model="pyannote/speaker-diarization-community-1",
        device="cuda",
        config_hash="abc123",
        payload={"chunk_start_sequence": 5, "chunk_end_sequence": 12},
    ) as metric:
        metric["payload"]["speaker_turn_count"] = 4

    event = events[0]
    assert event["stage"] == "rolling_diarization_window"
    assert event["recording_id"] == 99
    assert event["payload"]["window_start_s"] == 10.0
    assert event["payload"]["window_end_s"] == 35.0
    assert event["payload"]["window_duration_s"] == 25.0
    assert event["payload"]["window_index"] == 2
    assert event["payload"]["model"] == "pyannote/speaker-diarization-community-1"
    assert event["payload"]["device"] == "cuda"
    assert event["payload"]["config_hash"] == "abc123"
    assert event["payload"]["chunk_start_sequence"] == 5
    assert event["payload"]["chunk_end_sequence"] == 12
    assert event["payload"]["speaker_turn_count"] == 4


def test_score_turn_overlap_proxy_counts_matching_speaker_overlap():
    score = score_turn_overlap_proxy(
        [
            {"start": 0.0, "end": 2.0, "speaker": "speaker_a"},
            {"start": 2.0, "end": 4.0, "speaker": "speaker_b"},
        ],
        [
            {"start": 0.5, "end": 2.0, "speaker": "speaker_a"},
            {"start": 2.0, "end": 3.0, "speaker": "speaker_c"},
            {"start": 3.0, "end": 4.0, "speaker": "speaker_b"},
        ],
    )

    assert score == {
        "total_reference_s": 4.0,
        "matched_s": 2.5,
        "overlap_score": 0.625,
    }


def test_write_pipeline_baseline_report(tmp_path):
    report_path = tmp_path / "baseline.md"
    write_pipeline_baseline_report(
        {
            "event_count": 2,
            "asr_invocations": 1,
            "diarization_invocations": 1,
            "live_segments_emitted": 3,
            "live_transcript_reuse_count": 1,
            "final_asr_rerun_count": 0,
            "finalization_ms": 42.0,
            "stage_counts": {"live_asr_region": 1},
            "speaker_match_kinds": {"local_embedding": 1},
        },
        report_path,
    )

    content = report_path.read_text(encoding="utf-8")
    assert "# Pipeline Baseline Summary" in content
    assert "- ASR invocations: 1" in content
    assert "- `local_embedding`: 1" in content
