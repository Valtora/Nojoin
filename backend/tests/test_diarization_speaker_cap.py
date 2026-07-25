"""Tests for the per-recording speaker cap and the diarization stats metric.

The load-bearing property is that an unset cap leaves the diarization call
byte-identical to what it was before the feature existed: pyannote must receive
no speaker keyword at all, not ``max_speakers=None``.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

from backend.processing.diarization_stats import (
    speech_seconds_by_label,
    summarize_diarization_speakers,
)
from backend.processing.speaker_cap import (
    MAX_SPEAKER_CAP,
    MIN_SPEAKER_CAP,
    normalize_speaker_cap,
)

# --- speaker cap normalisation ---------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (1, 1),
        (2, 2),
        ("3", 3),
        (3.9, 3),  # truncated, not rounded
        (MAX_SPEAKER_CAP, MAX_SPEAKER_CAP),
        (MIN_SPEAKER_CAP, MIN_SPEAKER_CAP),
        (0, None),
        (-1, None),
        (MAX_SPEAKER_CAP + 1, None),
        ("not a number", None),
        ([], None),
    ],
)
def test_normalize_speaker_cap(value, expected):
    assert normalize_speaker_cap(value) == expected


def test_normalize_speaker_cap_rejects_booleans():
    # bool is an int subclass; True must not silently become a cap of 1.
    assert normalize_speaker_cap(True) is None
    assert normalize_speaker_cap(False) is None


# --- diarize_audio wiring ---------------------------------------------------


@dataclass
class _FakeSegment:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


class _FakeAnnotation:
    def __init__(self, tracks):
        self._tracks = tracks

    def itertracks(self, yield_label=False):
        for segment, label in self._tracks:
            yield (segment, None, label) if yield_label else (segment, None)

    def itersegments(self):
        return (segment for segment, _ in self._tracks)

    def labels(self):
        return sorted({label for _, label in self._tracks})

    def get_overlap(self):
        return []


class _RecordingPipeline:
    """Stands in for a loaded pyannote pipeline and records how it was called."""

    def __init__(self, annotation):
        self.annotation = annotation
        self.calls = []

    def __call__(self, audio_path, **kwargs):
        self.calls.append(kwargs)
        return self.annotation


@pytest.fixture
def diarize_module(monkeypatch, tmp_path):
    """Import backend.processing.diarize with its heavy dependencies stubbed."""
    for name in (
        "torch",
        "huggingface_hub",
        "pyannote",
        "pyannote.audio",
        "pyannote.core",
        "pyannote.audio.core",
        "pyannote.audio.core.task",
    ):
        monkeypatch.setitem(sys.modules, name, sys.modules.get(name) or _stub(name))

    monkeypatch.delitem(sys.modules, "backend.processing.diarize", raising=False)
    import backend.processing.diarize as diarize  # noqa: PLC0415

    return diarize


def _stub(name):
    module = types.ModuleType(name)
    if name == "torch":
        module.serialization = types.SimpleNamespace(add_safe_globals=lambda *a: None)
        module.cuda = types.SimpleNamespace(
            is_available=lambda: False, empty_cache=lambda: None
        )
        module.device = lambda spec: spec
    if name == "pyannote.core":
        module.Annotation = object
    if name == "pyannote.audio":
        module.Pipeline = object
    if name == "pyannote.audio.core.task":
        module.Problem = object
        module.Resolution = object
        module.Specifications = object
    if name == "huggingface_hub":
        module.login = lambda **kwargs: None
    return module


def _run_diarize(diarize, monkeypatch, tmp_path, max_speakers):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"RIFF")

    annotation = _FakeAnnotation(
        [
            (_FakeSegment(0.0, 10.0), "SPEAKER_00"),
            (_FakeSegment(10.0, 20.0), "SPEAKER_01"),
        ]
    )
    pipeline = _RecordingPipeline(annotation)

    monkeypatch.setattr(diarize, "_pipeline_cache", {}, raising=False)
    monkeypatch.setattr(diarize, "load_diarization_pipeline", lambda *a, **k: pipeline)
    monkeypatch.setattr(diarize, "_filter_short_segments", lambda ann, **k: ann)

    result = diarize.diarize_audio(
        str(audio),
        config={"processing_device": "cpu", "hf_token": "x"},
        max_speakers=max_speakers,
    )
    return pipeline, result


def test_auto_detect_passes_no_speaker_kwargs(diarize_module, monkeypatch, tmp_path):
    """An unset cap must reach pyannote exactly as before the feature existed."""
    pipeline, _ = _run_diarize(diarize_module, monkeypatch, tmp_path, None)
    assert pipeline.calls == [{}]


def test_cap_is_passed_as_max_speakers(diarize_module, monkeypatch, tmp_path):
    pipeline, _ = _run_diarize(diarize_module, monkeypatch, tmp_path, 2)
    assert pipeline.calls == [{"max_speakers": 2}]
    # num_speakers would force a split and cause the very bug this prevents.
    assert "num_speakers" not in pipeline.calls[0]
    assert "min_speakers" not in pipeline.calls[0]


@pytest.mark.parametrize("bad_cap", [0, -3, MAX_SPEAKER_CAP + 1, "abc"])
def test_out_of_range_cap_degrades_to_auto_detect(
    diarize_module, monkeypatch, tmp_path, bad_cap
):
    """A bad cap must not fail a recording that has already been captured."""
    pipeline, result = _run_diarize(diarize_module, monkeypatch, tmp_path, bad_cap)
    assert pipeline.calls == [{}]
    assert result is not None


# --- diarization stats ------------------------------------------------------


def _annotation():
    return _FakeAnnotation(
        [
            (_FakeSegment(0.0, 60.0), "SPEAKER_00"),
            (_FakeSegment(60.0, 90.0), "SPEAKER_01"),
            (_FakeSegment(90.0, 100.0), "SPEAKER_00"),
            (_FakeSegment(100.0, 102.0), "SPEAKER_02"),
        ]
    )


def test_summarize_orders_speakers_by_speech_and_reports_share():
    stats = summarize_diarization_speakers(_annotation())

    assert stats["speaker_count"] == 3
    assert stats["total_speech_s"] == pytest.approx(102.0)
    assert [s["label"] for s in stats["speakers"]] == [
        "SPEAKER_00",
        "SPEAKER_01",
        "SPEAKER_02",
    ]
    assert stats["speakers"][0]["speech_s"] == pytest.approx(70.0)
    assert stats["speakers"][0]["segment_count"] == 2
    assert stats["speakers"][0]["share"] == pytest.approx(70.0 / 102.0, abs=1e-4)
    # The small cluster is what tells a reader whether the phantom filter could
    # ever have helped.
    assert stats["speakers"][-1]["speech_s"] == pytest.approx(2.0)


def test_summarize_reports_cap_state():
    uncapped = summarize_diarization_speakers(_annotation())
    assert uncapped["cap_applied"] is False
    assert uncapped["cap_binding"] is False
    assert uncapped["max_speakers_requested"] is None

    capped = summarize_diarization_speakers(_annotation(), max_speakers=3)
    assert capped["cap_applied"] is True
    assert capped["cap_binding"] is True

    slack = summarize_diarization_speakers(_annotation(), max_speakers=9)
    assert slack["cap_applied"] is True
    assert slack["cap_binding"] is False


def test_summarize_handles_missing_annotation():
    stats = summarize_diarization_speakers(None)
    assert stats["speaker_count"] == 0
    assert stats["speakers"] == []
    assert stats["total_speech_s"] == 0


def test_speech_seconds_by_label_totals_per_speaker():
    totals = speech_seconds_by_label(_annotation())
    assert totals == pytest.approx(
        {"SPEAKER_00": 70.0, "SPEAKER_01": 30.0, "SPEAKER_02": 2.0}
    )


def test_speech_seconds_never_raises_for_unusable_input():
    """Survivor selection must degrade, not abort the whole merge pass."""

    class Broken:
        def itertracks(self, yield_label=False):
            raise RuntimeError("not a real annotation")

    assert speech_seconds_by_label(Broken()) == {}
    assert speech_seconds_by_label(None) == {}
