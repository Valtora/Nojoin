# Pipeline Baseline Report: 2026-05-19

Status: Phase 0 initial baseline harness implemented.

This report captures the current pipeline behavior that the refactor must improve or preserve. It is not a quality claim for diarization accuracy; realistic private audio fixtures still need to be run locally for quality measurements.

## Implemented Baseline Harness

- Synthetic fixture manifest and generator live in `backend/tests/pipeline_fixtures/`.
- Generated WAV files are intentionally ignored by git.
- Local realistic fixture manifests are supported through `NOJOIN_PIPELINE_FIXTURE_MANIFEST`.
- Passive pipeline metrics are emitted as structured log events with the `pipeline_metric` prefix.
- Optional JSONL capture is available through `NOJOIN_PIPELINE_METRICS_JSONL`.
- A lightweight known-turn overlap proxy is available for fixtures with reference speaker spans.
- Baseline tests are registered with the `pipeline_baseline` pytest marker.

## Current Behavior Baselines

- Live uploaded chunks are saved and can dispatch live transcription tasks, but upload cadence remains separate from diarization quality.
- Live transcription emits provisional JSON transcript segments from VAD-completed regions.
- Live speaker assignment currently resolves each region through short-region embeddings, global speaker matching, or fallback labels.
- A live speaker rename only helps future segments if the resolver keeps assigning that same `LIVE_XX` label.
- A per-segment live speaker assignment is recorded as a segment edit and does not yet become a durable future speaker mapping.
- Final processing can reuse live transcript text when no engine override is supplied.
- Final speaker mapping back to live labels is currently index-based; a baseline test documents that out-of-order final/live segment lists can map speakers incorrectly.
- Full final diarization still runs during normal final processing when diarization is enabled.

## Metrics Now Available

- `audio_chunk_uploaded`: uploaded sequence, byte count, and filename.
- `live_task_started`: live task entry and sequence.
- `live_task_skipped`: live task skip reason and recording status.
- `live_sequence_skipped`: sequence-gating skip reason.
- `live_run_started`: contiguous sequence run drained by a live task.
- `live_vad_classified`: speech-region count, completed-region count, buffer length, and carry cut point.
- `live_asr_region`: live ASR region timing, engine, prefix context, text character count, and elapsed time.
- `live_speaker_embedding_error`: embedding extraction failures.
- `live_speaker_resolved`: selected label, match kind, score where available, fallback label, and region duration.
- `live_segments_persisted`: emitted segment count, first/last segment timing, and last stable speaker label.
- `live_run_completed`: run completion, segment count, next expected sequence, and buffer start.
- `live_run_failed`: non-fatal live task failure details.
- `speaker_correction_applied`: user speaker rename or segment speaker correction events.
- `transcript_text_correction_applied`: user transcript text correction events.
- `final_transcription_reused_live`: final-stage reuse of live transcript text.
- `final_asr_invocation`: final-stage ASR rerun timing and engine metadata.
- `final_diarization_invocation`: final-stage Pyannote timing and availability.
- `rolling_diarization_window`: rolling Pyannote window timing contract for the Phase 4 worker.
- `final_live_reconciliation`: live/final mapping counts and manual edit preservation counts.
- `final_segments_built`: final segment count after consolidation.
- `final_processing_completed`: successful final processing duration.
- `final_processing_failed`: final processing failure details.

## How To Run

Quick Phase 0 tests:

```bash
pytest backend/tests/test_pipeline_metrics.py backend/tests/test_pipeline_baseline_metrics.py
```

Generate synthetic audio fixtures:

```bash
python backend/tests/pipeline_fixtures/generate_synthetic_fixtures.py \
  --manifest backend/tests/pipeline_fixtures/manifest.synthetic.json \
  --output-dir backend/tests/pipeline_fixtures/generated
```

Capture pipeline metric JSONL during a local recording run:

```bash
NOJOIN_PIPELINE_METRICS_JSONL=artifacts/pipeline-baseline/latest.jsonl docker compose up api worker
```

## Remaining Baseline Work

- Run realistic local meeting audio fixtures and save a local quality summary.
- Wire `rolling_diarization_window` timing into the real Phase 4 worker path when rolling diarization exists.
- Compare current finalization time against the unified finalization path once Phase 6 is implemented.
