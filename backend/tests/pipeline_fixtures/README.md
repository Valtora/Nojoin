# Pipeline Test Fixtures

Synthetic WAV fixtures for ingest, chunking, timing, fallback and degraded-mode smoke tests. Generated audio is not committed: `generated/` is git-ignored.

## Synthetic Fixtures

```bash
python backend/tests/pipeline_fixtures/generate_synthetic_fixtures.py
```

This renders every fixture in `manifest.synthetic.json` into `generated/` as 16 kHz mono WAV; `--manifest` and `--output-dir` override both. Each speaker is a pure tone, so the files are no use for transcription accuracy.

`backend/tests/test_pipeline_baseline_metrics.py` (marker `pipeline_baseline`) checks that the synthetic manifest covers the baseline scenarios and that the generator writes valid WAVs.

## Manifest Fields

- `id`: stable fixture identifier.
- `path`: where the WAV lives. The generator keeps only the file name and writes it into the output directory.
- `kind`: `live_recording` or `imported_recording`.
- `expected_speakers`: expected speaker count, when known.
- `expected_language`: language hint, when known.
- `scenarios`: tags naming what the fixture covers. The generator reads two: `quiet_speaker` lowers `speaker_b`, and `noise` adds a low hum.
- `known_turns`: time-aligned speaker spans (`start`, `end`, `speaker`). The generator renders `speaker_a`, `speaker_b` and `speaker_c` as different tones.
- `notes`: human-readable context.

`manifest.example.json` shows the same shape for a private manifest that points at real recordings outside git. Nothing in the repository reads such a manifest; never commit the audio it points at.
