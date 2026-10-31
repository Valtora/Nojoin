"""The final pass hands the merge the spans of lines a person edited (#813)."""

from __future__ import annotations

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.processing.sentence_start import EDITED_LINE_SPANS
from backend.tests.test_pipeline_stages import (
    _ExecResult,
    _FakeRecording,
    _FakeSession,
    _FakeTranscript,
    _install,
    _install_happy_path_modules,
    _NoTurns,
    _run_task,
)


def _line(start_ms: int, end_ms: int, **edits) -> TranscriptUtterance:
    return TranscriptUtterance(
        recording_id=741,
        start_ms=start_ms,
        end_ms=end_ms,
        state=TranscriptUtteranceState.PROVISIONAL,
        text="words",
        **edits,
    )


class _LinesSession(_FakeSession):
    def __init__(self, recording, transcript, lines):
        super().__init__(recording, transcript)
        self.lines = lines

    def exec(self, statement=None, *args, **kwargs):
        if "transcript_utterance" in str(statement).lower():
            return _ExecResult(first=None, all_=self.lines)
        return super().exec(statement, *args, **kwargs)


def test_the_merge_is_given_every_edited_lines_span(monkeypatch):
    """A chosen speaker or protected wording or splits mark a line as edited;
    a machine line does not."""
    lines = [
        _line(0, 900),
        _line(1000, 1500, manual_speaker_locked=True),
        _line(2000, 2600, manual_text_locked=True),
        _line(3000, 3400, confidence_payload={"human_protection": {"version": 1}}),
    ]
    session = _LinesSession(_FakeRecording(741), _FakeTranscript(741), lines)
    _install_happy_path_modules(monkeypatch, diarization_result=_NoTurns())
    _install(
        monkeypatch,
        "backend.processing.phantom_filter",
        filter_phantom_speakers=lambda diar, *a, **k: diar,
    )
    seen: dict = {}

    def _capture_combine(transcription, diarization, config=None):
        seen["config"] = config
        return []

    _install(
        monkeypatch,
        "backend.utils.transcript_utils",
        combine_transcription_diarization=_capture_combine,
        consolidate_diarized_transcript=lambda segs, *a, **k: list(segs),
    )

    _run_task(monkeypatch, session, recording_id=741)

    assert seen["config"][EDITED_LINE_SPANS] == [
        (1000, 1500),
        (2000, 2600),
        (3000, 3400),
    ]
