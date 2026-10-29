"""Structured extraction contracts and opt-in finalisation behaviour."""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from backend.celery_app import celery_app
from backend.models.notes_output import NotesOutput
from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.processing.llm_backends.base import LLMBackend
from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.tests.sqlite_schemas import NOTES_OUTPUTS_SCHEMA, TIMELINE_NOTES_SCHEMA
from backend.tests.test_automatic_meeting_intelligence_worker import (
    _create_worker_ai_database,
    _sample_llm_config,
    _set_owner_settings,
)
from backend.utils.glossary_suggestions import (
    glossary_extraction_spec,
    parse_glossary_suggestions,
)
from backend.utils.long_meeting_notes import (
    PartsBudget,
    context_for_notes,
    generate_notes_in_parts,
)
from backend.utils.meeting_intelligence import AutomaticMeetingIntelligenceResult
from backend.utils.meeting_notes import NotesPromptContext
from backend.worker import tasks
from backend.worker.tasks.notes_outputs import queue_automatic_glossary


def test_parser_filters_bad_entries_and_deduplicates_by_term() -> None:
    answer = json.dumps(
        [
            "api: duplicate",
            "New: useful",
            "NEW: duplicate",
            "bare word",
            "Empty:",
            3,
            {"term": "bad"},
            "Bad\nterm: invalid",
            "Bad\u2028term: invalid",
            "Bad\u2029term: invalid",
            "Bad\u0085term: invalid",
            "* Bullet: invalid",
            "Kubernetes: duplicate correction",
            "Product: duplicate correction",
            "Grafana: duplicate correction",
            "Last: kept",
            "Over: limit",
        ]
    )
    assert (
        parse_glossary_suggestions(
            answer,
            limit=2,
            excluded='API = application interface\nKubernetes, heard as "cubanetties"\nProduct: a tool, heard as "pro duct"\n- Grafana, HEARD AS "graph anna": a correction',
        )
        == "New: useful\nLast: kept"
    )


@pytest.mark.parametrize(
    "answer",
    [
        '```json\n["SLO: service level objective"]\n```',
        'Here you go:\n["SLO: service level objective"]\nLet me know if you need more.',
    ],
)
def test_parser_reads_the_array_inside_a_fence_or_commentary(answer: str) -> None:
    assert (
        parse_glossary_suggestions(answer, limit=15) == "SLO: service level objective"
    )


@pytest.mark.parametrize("answer", ["not JSON", '{"terms": "none"}', "null", "] ["])
def test_parser_rejects_an_answer_without_a_list_in_plain_words(answer: str) -> None:
    with pytest.raises(ValueError, match="did not return a list of terms"):
        parse_glossary_suggestions(answer, limit=15)


def test_json_contract_survives_whole_meeting_and_parts_prompts() -> None:
    context = NotesPromptContext(
        glossary="Known: already defined",
        output_spec_override=glossary_extraction_spec(5, "Known: already defined"),
    )
    prompts = [
        LLMBackend.build_notes_prompt(None, "Transcript", {}, notes_context=context)
    ]
    long_context = context_for_notes("", None, None, None, context)

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps([f"Term{i}: supported meaning" for i in range(8)])

    answer = generate_notes_in_parts(
        "\n".join(
            f"[00:00 - 00:01] Dana: Term{i} has a supported meaning."
            for i in range(900)
        ),
        long_context,
        complete,
        PartsBudget(window=6000, tokens_per_byte=0.3),
    )
    assert len(prompts) > 3  # Drafts and their combining call both ran.
    assert len(parse_glossary_suggestions(answer, limit=5).splitlines()) == 5
    for prompt in prompts:
        assert "Return only the JSON array" in prompt
        assert "Do not add a glossary section" not in prompt
        assert "Write Markdown" not in prompt
        assert "## Summary" not in prompt


@pytest.mark.parametrize(
    "enabled, dispatch_fails", [(False, False), (True, False), (True, True)]
)
def test_automatic_stage_queues_the_glossary_after_the_notes_are_saved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_celery_dispatch: list[tuple[str, Any, Any]],
    enabled: bool,
    dispatch_fails: bool,
) -> None:
    engine = _create_worker_ai_database(tmp_path)
    with engine.begin() as connection:
        connection.execute(text(NOTES_OUTPUTS_SCHEMA))
        for statement in TIMELINE_NOTES_SCHEMA.strip().split(";\n\n"):
            connection.execute(text(statement))
        _set_owner_settings(connection, {"glossary_suggestions_enabled": enabled})
    config = _sample_llm_config()
    # Global/merged preference must never opt a recording's owner in.
    config.merged_config["glossary_suggestions_enabled"] = True

    class LLM:
        def generate_meeting_intelligence(
            self, *args: Any, **kwargs: Any
        ) -> AutomaticMeetingIntelligenceResult:
            return AutomaticMeetingIntelligenceResult(
                speaker_mapping={},
                title="Completed meeting",
                notes_markdown="## Summary\nPrimary result.",
            )

    if dispatch_fails:

        def broken_dispatch(*args: Any, **kwargs: Any) -> None:
            raise ConnectionError("broker unavailable")

        monkeypatch.setattr(celery_app, "send_task", broken_dispatch)
    monkeypatch.setattr(tasks, "_llm_backend_from_config", lambda _: LLM())
    try:
        with Session(engine) as session:
            recording = session.get(Recording, 1)
            transcript = session.exec(select(Transcript)).one()
            result = tasks._run_automatic_meeting_intelligence_stage(
                session=session,
                task=None,
                recording=recording,
                transcript=transcript,
                speakers=session.exec(select(RecordingSpeaker)).all(),
                transcript_text="Dana explained the SLO.",
                unresolved_speakers=(),
                llm_config=config,
                prefer_short_titles=True,
                device_suffix="",
                timeline_notes=RecordingTimelineNotes(),
            )
            assert result is not None
            assert transcript.notes_status == "completed"
            assert transcript.notes == "## Summary\nPrimary result."
            outputs = session.exec(select(NotesOutput)).all()
            assert len(outputs) == int(enabled)
            if not enabled:
                assert stub_celery_dispatch == []
                return
            if dispatch_fails:
                assert outputs[0].notes_status == "error"
                return
            assert outputs[0].notes_status == "generating"
            assert stub_celery_dispatch == [
                (
                    "backend.worker.tasks.generate_notes_task",
                    [1, None, outputs[0].id],
                    None,
                )
            ]
            # A reprocess leaves the existing Glossary output alone.
            queue_automatic_glossary(session, recording)
            assert len(session.exec(select(NotesOutput)).all()) == 1
            assert len(stub_celery_dispatch) == 1
    finally:
        engine.dispose()
