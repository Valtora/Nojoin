# Pipeline Fixture Harness

This directory defines the Phase 0 baseline fixture harness for the unified transcription and diarization pipeline refactor.

The repository intentionally does not commit generated audio files. Use the synthetic manifest and generator for local smoke fixtures, and create a private local manifest for realistic meeting recordings when measuring diarization quality.

## Synthetic Fixtures

Generate timing-oriented WAV fixtures with:

```bash
python backend/tests/pipeline_fixtures/generate_synthetic_fixtures.py \
  --manifest backend/tests/pipeline_fixtures/manifest.synthetic.json \
  --output-dir backend/tests/pipeline_fixtures/generated
```

The generated tones are not useful for transcription accuracy, but they are useful for ingest, chunking, timing, fallback, and degraded-mode smoke tests.

## Realistic Local Fixtures

For speaker quality baselines, create a local manifest that points at private audio files outside git. The manifest shape matches `manifest.example.json`. A realistic baseline should include:

- Single-speaker clean speech.
- Two-speaker alternating turns.
- Three-or-more-speaker meetings.
- Overlap and interruptions.
- Long monologues.
- Quiet speakers and noisy rooms.
- Late-joining speakers.
- Live user rename and segment-assignment scenarios.
- Imported recordings.
- Missing or invalid Hugging Face token paths.

Run local baselines with:

```bash
NOJOIN_PIPELINE_FIXTURE_MANIFEST=/path/to/local-manifest.json pytest backend/tests/test_pipeline_baseline_metrics.py
```

## Manifest Fields

- `id`: Stable fixture identifier.
- `path`: WAV path relative to the manifest file, or an absolute local path.
- `kind`: `live_recording` or `imported_recording`.
- `expected_speakers`: Expected speaker count when known.
- `expected_language`: Language hint when known.
- `scenarios`: Tags used to select fixture categories.
- `known_turns`: Optional time-aligned speaker spans for diarization scoring.
- `notes`: Human-readable fixture context.
