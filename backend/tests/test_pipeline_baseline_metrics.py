import importlib.util
import json
import wave
from pathlib import Path

import pytest

from backend.processing.pipeline_metrics import summarize_pipeline_metrics

FIXTURE_DIR = Path(__file__).parent / "pipeline_fixtures"


def _load_manifest(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.pipeline_baseline
def test_synthetic_manifest_covers_phase_zero_scenarios():
    manifest = _load_manifest("manifest.synthetic.json")
    scenarios = {
        scenario
        for fixture in manifest["fixtures"]
        for scenario in fixture.get("scenarios", [])
    }

    assert {
        "single_speaker",
        "alternating_turns",
        "multi_speaker",
        "overlap",
        "long_monologue",
        "quiet_speaker",
        "late_speaker",
        "user_rename",
        "global_speaker_assignment",
        "import",
        "missing_hf_token",
    }.issubset(scenarios)


@pytest.mark.pipeline_baseline
def test_synthetic_fixture_generator_writes_valid_wavs(tmp_path):
    generator_path = FIXTURE_DIR / "generate_synthetic_fixtures.py"
    spec = importlib.util.spec_from_file_location(
        "generate_synthetic_fixtures", generator_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    generated = module.generate_fixtures(
        FIXTURE_DIR / "manifest.synthetic.json", tmp_path
    )

    assert len(generated) == len(_load_manifest("manifest.synthetic.json")["fixtures"])
    with wave.open(str(generated[0]), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getframerate() == 16_000
        assert wav_file.getnframes() > 0


@pytest.mark.pipeline_baseline
def test_baseline_summary_exposes_asr_reuse_and_rerun_counts():
    summary = summarize_pipeline_metrics(
        [
            {"stage": "final_transcription_reused_live", "status": "ok", "payload": {}},
            {"stage": "final_asr_invocation", "status": "ok", "payload": {}},
            {
                "stage": "live_segments_persisted",
                "status": "ok",
                "payload": {"segment_count": 4},
            },
        ]
    )

    assert summary["live_transcript_reuse_count"] == 1
    assert summary["final_asr_rerun_count"] == 1
    assert summary["live_segments_emitted"] == 4
