"""Prompt-only attribution names never leak the stored UNKNOWN sentinel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

import backend.utils.llm_config as llm_config_module
import backend.worker.tasks as tasks_module
from backend.api.v1.endpoints.transcripts.helpers import _format_transcript_text
from backend.models.pipeline import ProcessingRunKind
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.tests.storage_hygiene_harness import add_recording, postgres_engine
from backend.tests.test_infer_speakers_task import (
    _create_infer_speakers_task_database,
    _run_infer_speakers_task,
)
from backend.utils.canonical_pipeline import append_utterances_from_segments
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
    postgres_test_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.processing.text_embedding as text_embedding
    from backend.models.context_chunk import TEXT_EMBEDDING_DIMENSIONS, ContextChunk

    embedded: list[str] = []

    class Embeddings:
        def embed(self, texts: list[str]) -> list[list[float]]:
            embedded.extend(texts)
            return [[0.0] * TEXT_EMBEDDING_DIMENSIONS for _ in texts]

    monkeypatch.setattr(text_embedding, "get_text_embedding_service", Embeddings)
    with postgres_engine(postgres_test_url) as engine:
        recording_id = add_recording(engine, "meeting.wav")
        with Session(engine) as session:
            session.add(Transcript(recording_id=recording_id, segments=[]))
            for label, name in SPEAKER_MAP.items():
                session.add(
                    RecordingSpeaker(
                        recording_id=recording_id, diarization_label=label, name=name
                    )
                )
            append_utterances_from_segments(
                session,
                recording_id=recording_id,
                segments=SEGMENTS,
                run_kind=ProcessingRunKind.FINALIZE,
                source="final",
            )
            session.commit()
        with Session(engine) as session:
            monkeypatch.setattr(tasks_module.index_transcript_task, "_session", session)
            tasks_module.index_transcript_task.run(recording_id)
            (chunk,) = session.exec(select(ContextChunk)).all()

    assert embedded == [
        "Unidentified speaker: UNKNOWN is the stored label.\nPriya: A reply.\n"
    ]
    assert chunk.content == embedded[0]
    assert set(chunk.meta["speakers"]) == {"UNKNOWN", "Priya"}


def test_ai_analytics_attributes_an_unidentified_line_through_its_prompt_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.tests import test_meeting_analysis_task as analysis

    engine = analysis._make_engine(tmp_path, "unidentified")
    with engine.begin() as connection:
        analysis._seed(connection)
        # A live line no speaker was assigned to: analytics reads it as UNKNOWN.
        connection.execute(
            text(
                "INSERT INTO transcript_utterances (id, created_at, updated_at, "
                "public_id, recording_id, sort_key, start_ms, end_ms, text, "
                "speaker_label, recording_speaker_id, state, source_kind, revision, "
                "manual_text_locked, manual_speaker_locked, speaker_assignment_source, "
                "speaker_assignment_authority, overlap_rank) VALUES "
                "(4, :ts, :ts, 'u-3', 10, '0003', 18000, 24000, :body, 'UNKNOWN', "
                "NULL, 'provisional', 'live', 1, 0, 0, 'live', 'live', 0)"
            ),
            {"ts": analysis._now(), "body": "Nobody claimed this line."},
        )
    response = {
        "topics": [],
        "questions": [],
        "decisions": [],
        "sentiment": [
            {
                "speaker": "Unidentified speaker",
                "tone": "neutral",
                "summary": "Spoke once.",
                "citations": [
                    {"quote": "Nobody claimed this line.", "start_seconds": 18}
                ],
            }
        ],
    }
    backend = analysis.FakeBackend(json.dumps(response))
    analysis._run(engine, monkeypatch, config=analysis._configured(), backend=backend)

    assert "Unidentified speaker" in backend.last_request.allowlist.names
    assert backend.last_request.transcript.endswith(
        "[00:18 - 00:24] Unidentified speaker: Nobody claimed this line."
    )
    block = analysis._payload(analysis._row(engine)[2])["ai"]
    # The model's answer, given the prompt name, maps back to the stored label.
    assert [item["speaker_key"] for item in block["sentiment"]] == ["label:UNKNOWN"]
    assert block["excluded"]["unknown_speaker_items"] == 0
