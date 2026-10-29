"""Prompt-only attribution names never leak the stored UNKNOWN sentinel."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session

import backend.utils.llm_config as llm_config_module
import backend.worker.tasks as tasks_module
from backend.api.v1.endpoints.transcripts.helpers import _format_transcript_text
from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.tests.test_infer_speakers_task import (
    _create_infer_speakers_task_database,
    _run_infer_speakers_task,
)
from backend.utils.live_speaker_labels import (
    render_indexed_transcript_for_llm,
    speaker_display_name,
    speaker_prompt_name,
)
from backend.utils.meeting_notes import format_segments_for_llm
from backend.utils.speaker_name_suggestions import SpeakerInferenceResult
from backend.worker.tasks.meeting_edge_stage import (
    _build_recent_meeting_edge_transcript,
)

SEGMENTS = [
    {
        "speaker": "UNKNOWN",
        "start": 0.0,
        "end": 2.0,
        "text": "UNKNOWN is the stored label.",
        "overlapping_speakers": ["UNKNOWN", "SPEAKER_00", "LIVE_PROVISIONAL_3"],
    },
    {"speaker": "SPEAKER_01", "start": 2.0, "end": 4.0, "text": "A reply."},
]
SPEAKER_MAP = {"SPEAKER_00": "Dana", "SPEAKER_01": "Priya"}


def test_prompt_name_preserves_known_live_and_unmapped_speakers() -> None:
    assert (
        speaker_prompt_name("UNKNOWN", {"UNKNOWN": "UNKNOWN"}) == "Unidentified speaker"
    )
    assert speaker_prompt_name("SPEAKER_00", SPEAKER_MAP) == "Dana"
    assert speaker_prompt_name("LIVE_PROVISIONAL_3", {}) == "Speaker 3"
    assert (
        speaker_prompt_name("LIVE_PROVISIONAL_3", {"LIVE_PROVISIONAL_3": "Ada"})
        == "Ada"
    )
    assert speaker_prompt_name("SPEAKER_02", {}) == "SPEAKER_02"
    assert speaker_display_name("UNKNOWN", {}) == "UNKNOWN"


@pytest.mark.parametrize("with_end", [True, False])
def test_shared_prompt_formatter_names_primary_and_overlapping_unknown(
    with_end: bool,
) -> None:
    rendered = format_segments_for_llm(SEGMENTS, SPEAKER_MAP, with_end=with_end)
    assert (
        "Unidentified speaker (with Unidentified speaker, Dana, Speaker 3):" in rendered
    )
    assert "UNKNOWN is the stored label." in rendered
    assert "Priya: A reply." in rendered


def test_mcp_transcript_text_names_unknown_as_unidentified() -> None:
    assert _format_transcript_text(SEGMENTS, SPEAKER_MAP).splitlines() == [
        "[00:00] Unidentified speaker: UNKNOWN is the stored label.",
        "[00:02] Priya: A reply.",
    ]


def test_meeting_edge_uses_prompt_names() -> None:
    rendered = _build_recent_meeting_edge_transcript(
        SEGMENTS, SPEAKER_MAP, RecordingTimelineNotes()
    )
    assert (
        "Unidentified speaker (with Unidentified speaker, Dana, Speaker 3):" in rendered
    )


def test_automatic_summary_preserves_inference_keys_but_names_unknown() -> None:
    rendered = tasks_module._build_automatic_meeting_intelligence_transcript(
        SEGMENTS, SPEAKER_MAP, ("SPEAKER_01", "UNKNOWN"), RecordingTimelineNotes()
    )
    assert (
        "Unidentified speaker (with Unidentified speaker, Dana, Speaker 3):" in rendered
    )
    assert "SPEAKER_01: A reply." in rendered
    assert "UNKNOWN is the stored label." in rendered


def test_old_indexed_chunks_resolve_only_speaker_prefixes() -> None:
    rendered = render_indexed_transcript_for_llm(
        "UNKNOWN: Say UNKNOWN: and SPEAKER_00: verbatim.\n"
        "SPEAKER_00: A reply.\n"
        "SPEAKER_02: Unmapped.\n",
        SPEAKER_MAP,
    )
    assert rendered == (
        "Unidentified speaker: Say UNKNOWN: and SPEAKER_00: verbatim.\n"
        "Dana: A reply.\n"
        "SPEAKER_02: Unmapped.\n"
    )


def test_independent_inference_names_unknown_without_changing_label_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _create_infer_speakers_task_database(
        tmp_path,
        owner_settings={
            "llm_provider": "openai",
            "openai_api_key": "sk-test",
            "openai_model": "gpt-test",
        },
        transcript_segments=SEGMENTS,
    )
    captured: dict[str, Any] = {}

    class FakeLLM:
        def infer_speaker_suggestions(
            self, transcript: str, **kwargs: Any
        ) -> SpeakerInferenceResult:
            captured["transcript"] = transcript
            captured["eligible_labels"] = kwargs["eligible_labels"]
            return SpeakerInferenceResult()

    monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(llm_config_module.config_manager, "get_all", lambda: {})
    monkeypatch.setattr(
        tasks_module, "_llm_backend_from_config", lambda config: FakeLLM()
    )
    _run_infer_speakers_task(engine)

    assert (
        " - Unidentified speaker - UNKNOWN is the stored label."
        in captured["transcript"]
    )
    assert " - SPEAKER_01 - A reply." in captured["transcript"]
    assert captured["eligible_labels"] == ("SPEAKER_00",)


def test_new_search_chunks_embed_prompt_names_and_keep_display_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.processing.text_embedding as text_embedding
    import backend.worker.tasks.intelligence as intelligence

    session = MagicMock()
    session.get.return_value = SimpleNamespace(id=1)
    session.exec.side_effect = [
        MagicMock(first=lambda: SimpleNamespace(segments=SEGMENTS)),
        MagicMock(all=lambda: []),
        MagicMock(all=lambda: []),
    ]
    embeddings = MagicMock()
    embeddings.embed.return_value = [[0.0] * 384]
    monkeypatch.setattr(tasks_module.index_transcript_task, "_session", session)
    monkeypatch.setattr(
        tasks_module,
        "build_transcript_segments_for_read",
        lambda *args, **kwargs: SEGMENTS,
    )
    monkeypatch.setattr(
        intelligence, "with_live_speaker_names", lambda *args: SPEAKER_MAP
    )
    monkeypatch.setattr(
        text_embedding, "get_text_embedding_service", lambda: embeddings
    )

    tasks_module.index_transcript_task.run(1)

    embedded = embeddings.embed.call_args.args[0]
    assert embedded == [
        "Unidentified speaker: UNKNOWN is the stored label.\nPriya: A reply.\n"
    ]
    chunk = session.add.call_args.args[0]
    assert chunk.content == embedded[0]
    assert set(chunk.meta["speakers"]) == {"UNKNOWN", "Priya"}
    session.commit.assert_called_once()


def test_ai_analytics_prompt_and_allowlist_agree_without_changing_display_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.services.meeting_analytics as analytics
    import backend.utils.canonical_pipeline as canonical
    from backend.worker.tasks.analytics_ai import _build_analysis_inputs

    directory = [
        {
            "speaker_key": "label:UNKNOWN",
            "diarization_label": "UNKNOWN",
            "name": "UNKNOWN",
        }
    ]
    utterance = SimpleNamespace(
        text="Unattributed speech.",
        start_ms=0,
        end_ms=4000,
        recording_speaker_id=None,
        speaker_label="UNKNOWN",
        state="finalized",
    )
    monkeypatch.setattr(
        analytics, "compute_recording_analytics", lambda *args: {"speakers": directory}
    )
    monkeypatch.setattr(canonical, "list_active_utterances", lambda *args: [utterance])

    inputs = _build_analysis_inputs(
        MagicMock(), SimpleNamespace(id=1, duration_seconds=4)
    )

    assert inputs["allowlist"].names == ("Unidentified speaker",)
    assert inputs["allowlist"].resolve("Unidentified speaker") == "label:UNKNOWN"
    assert (
        inputs["transcript"]
        == "[00:00 - 00:04] Unidentified speaker: Unattributed speech."
    )
    assert directory[0]["name"] == "UNKNOWN"
