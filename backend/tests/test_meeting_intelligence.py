import json
from types import SimpleNamespace

from backend.processing.llm_backends.base import LLMBackend
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    AutomaticMeetingIntelligenceResult,
    MeetingIntelligenceContractError,
    build_automatic_meeting_intelligence_prompt,
    build_automatic_meeting_intelligence_request,
    finalise_automatic_meeting_intelligence_result,
    get_speakers_eligible_for_llm_renaming,
    parse_automatic_meeting_intelligence_response,
)
from backend.utils.meeting_notes import (
    NOTES_BODY_SPEC,
    is_placeholder_speaker_name,
    resolve_recording_speaker_name,
)


def test_parse_automatic_meeting_intelligence_response_from_json() -> None:
    response = json.dumps(
        {
            "speaker_mapping": {"SPEAKER_00": "Alex"},
            "title": "Launch Readiness Review",
            "notes_markdown": "# Meeting Notes\n\n## Summary\nAll teams are ready.",
        }
    )

    result = parse_automatic_meeting_intelligence_response(response)

    assert result.speaker_mapping == {"SPEAKER_00": "Alex"}
    assert result.title == "Launch Readiness Review"
    # A redundant title heading is stripped; notes begin at the Summary section.
    assert result.notes_markdown.startswith("## Summary")
    assert "# Meeting Notes" not in result.notes_markdown


def test_parse_automatic_meeting_intelligence_response_from_fenced_json() -> None:
    payload = json.dumps(
        {
            "speaker_mapping": {
                "SPEAKER_00": "Alex",
                "SPEAKER_01": "Jordan",
            },
            "title": "Launch Readiness Review",
            "notes_markdown": "# Meeting Notes\n\n## Summary\nAll teams are ready.",
        },
        indent=2,
    )
    response = f"Here is the requested payload.\n\n```json\n{payload}\n```"

    result = parse_automatic_meeting_intelligence_response(response)

    assert result.speaker_mapping == {
        "SPEAKER_00": "Alex",
        "SPEAKER_01": "Jordan",
    }


def test_parse_automatic_meeting_intelligence_response_rejects_missing_field() -> None:
    response = json.dumps(
        {
            "speaker_mapping": {},
            "title": "Launch Readiness Review",
        }
    )

    try:
        parse_automatic_meeting_intelligence_response(response)
    except MeetingIntelligenceContractError as exc:
        assert "notes_markdown" in str(exc)
    else:
        raise AssertionError("Expected MeetingIntelligenceContractError")


def test_parse_automatic_meeting_intelligence_response_rejects_malformed_json() -> None:
    response = '{"speaker_mapping": {"SPEAKER_00": "Alex"}, "title": "Launch"'

    try:
        parse_automatic_meeting_intelligence_response(response)
    except MeetingIntelligenceContractError as exc:
        assert "Could not parse" in str(exc)
    else:
        raise AssertionError("Expected MeetingIntelligenceContractError")


def test_parse_automatic_meeting_intelligence_response_rejects_unknown_labels() -> None:
    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript="[00:00 - 00:05] Speaker 1: Status update.",
        unresolved_speakers=("SPEAKER_00",),
    )
    response = json.dumps(
        {
            "speaker_mapping": {"SPEAKER_01": "Jordan"},
            "title": "Launch Readiness Review",
            "notes_markdown": "# Meeting Notes\n\n## Summary\nAll teams are ready.",
        }
    )

    try:
        parse_automatic_meeting_intelligence_response(response, request=request)
    except MeetingIntelligenceContractError as exc:
        assert "not unresolved" in str(exc)
    else:
        raise AssertionError("Expected MeetingIntelligenceContractError")


def test_automatic_meeting_intelligence_request_rejects_duplicate_labels() -> None:
    try:
        AutomaticMeetingIntelligenceRequest(
            resolved_transcript="[00:00 - 00:05] Speaker 1: Status update.",
            unresolved_speakers=("SPEAKER_00", "SPEAKER_00"),
        )
    except MeetingIntelligenceContractError as exc:
        assert "duplicates" in str(exc)
    else:
        raise AssertionError("Expected MeetingIntelligenceContractError")


def test_automatic_meeting_intelligence_result_requires_a_section_heading() -> None:
    try:
        AutomaticMeetingIntelligenceResult(
            speaker_mapping={"SPEAKER_00": "Alex"},
            title="Launch Readiness Review",
            notes_markdown="All teams are ready.",
        )
    except MeetingIntelligenceContractError as exc:
        assert "section heading" in str(exc)
    else:
        raise AssertionError("Expected MeetingIntelligenceContractError")


def test_automatic_meeting_intelligence_result_accepts_summary_first_notes() -> None:
    result = AutomaticMeetingIntelligenceResult(
        speaker_mapping={"SPEAKER_00": "Alex"},
        title="Launch Readiness Review",
        notes_markdown="## Summary\nAll teams are ready.",
    )

    assert result.notes_markdown.startswith("## Summary")


def test_both_notes_prompts_embed_the_shared_notes_body_spec() -> None:
    # Drift guard: the unified meeting-intelligence prompt and the standalone
    # regeneration prompt must both embed NOTES_BODY_SPEC verbatim so their note
    # structure and fidelity rules can never diverge again.
    unified = build_automatic_meeting_intelligence_prompt(
        AutomaticMeetingIntelligenceRequest(
            resolved_transcript="SPEAKER_00: Hello.",
            unresolved_speakers=(),
        )
    )

    assert NOTES_BODY_SPEC in unified
    assert NOTES_BODY_SPEC in LLMBackend.build_notes_prompt(
        None, "SPEAKER_00: Hello.", {}
    )


def test_notes_body_spec_uses_the_best_practice_section_order() -> None:
    # Structure follows the approved best-practice layout: summary first, then
    # decisions and action items surfaced ahead of the per-topic detail.
    positions = [
        NOTES_BODY_SPEC.index(marker)
        for marker in (
            "## Summary",
            "## Key Decisions",
            "## Action Items / Tasks",
            "## Detailed Notes",
            "## Miscellaneous",
        )
    ]
    assert positions == sorted(positions)
    assert "Never invent facts" in NOTES_BODY_SPEC
    # Notes must begin at Summary; the app shows the meeting title separately.
    assert "do not add a title heading" in NOTES_BODY_SPEC.lower()


def test_notes_body_spec_prescribes_tables_for_decisions_and_actions() -> None:
    # Decisions and action items are tabular by nature, so the spec asks for
    # Markdown tables rather than lists. The editor and both document exporters
    # render these as real tables (issue #136).
    decisions = NOTES_BODY_SPEC.index("## Key Decisions")
    actions = NOTES_BODY_SPEC.index("## Action Items / Tasks")
    detail = NOTES_BODY_SPEC.index("## Detailed Notes")

    assert "| Decision | Rationale |" in NOTES_BODY_SPEC[decisions:actions]
    assert "| Action | Owner | Due |" in NOTES_BODY_SPEC[actions:detail]
    # Both tables need the delimiter row or they are not tables at all.
    assert NOTES_BODY_SPEC.count("| --- |") >= 1


def test_notes_body_spec_states_the_table_constraints() -> None:
    # These constraints keep generated tables inside what Markdown storage and
    # the DOCX and PDF exporters can carry: a merged cell or a real newline in a
    # cell would drop the table out of one surface or another.
    assert "never exceed six columns" in NOTES_BODY_SPEC
    assert "<br>" in NOTES_BODY_SPEC
    assert "Never merge or split cells" in NOTES_BODY_SPEC


def test_notes_body_spec_stays_safe_for_str_format() -> None:
    # Both embedding templates run str.format over the spec, so a stray brace
    # would raise at prompt-render time rather than in review.
    assert "{" not in NOTES_BODY_SPEC
    assert "}" not in NOTES_BODY_SPEC


def test_rendered_unified_prompt_carries_the_new_structure() -> None:
    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript="[00:00 - 00:05] Alex: We will ship on Friday.",
        unresolved_speakers=(),
    )
    prompt = build_automatic_meeting_intelligence_prompt(request)
    assert "## Key Decisions" in prompt
    assert "## Summary" in prompt


def test_build_automatic_meeting_intelligence_prompt_includes_shared_sections() -> None:
    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript="[00:00 - 00:05] Speaker 1: Status update.",
        unresolved_speakers=("SPEAKER_00", "SPEAKER_02"),
        user_notes="Confirm the rollout date",
        prefer_short_titles=True,
        output_language_instruction=(
            "Write the meeting title and notes in English (British). Use British spelling."
        ),
    )

    prompt = build_automatic_meeting_intelligence_prompt(request)

    assert "Only these diarization labels may appear in `speaker_mapping`:" in prompt
    assert "- SPEAKER_00" in prompt
    assert "- SPEAKER_02" in prompt
    assert "Confirm the rollout date" in prompt
    assert "3-5 words" in prompt
    assert "Return valid JSON only" in prompt
    assert "English (British)" in prompt
    assert "Keep any JSON keys exactly as specified" in prompt


def test_automatic_meeting_intelligence_strips_localized_title_heading() -> None:
    result = AutomaticMeetingIntelligenceResult(
        speaker_mapping={},
        title="Préparation du lancement",
        notes_markdown="# Notes de réunion\n\n## Résumé\nToutes les équipes sont prêtes.",
    )

    # The localized title heading is stripped; notes begin at the localized Summary.
    assert result.notes_markdown.startswith("## Résumé")
    assert "Notes de réunion" not in result.notes_markdown


def test_get_speakers_eligible_for_llm_renaming_excludes_trusted_speakers() -> None:
    speakers = [
        SimpleNamespace(
            diarization_label="SPEAKER_00",
            local_name=None,
            name="Speaker 1",
            global_speaker_id=None,
            global_speaker=None,
            merged_into_id=None,
        ),
        SimpleNamespace(
            diarization_label="SPEAKER_01",
            local_name="Alice",
            name="Alice",
            global_speaker_id=None,
            global_speaker=None,
            merged_into_id=None,
        ),
        SimpleNamespace(
            diarization_label="SPEAKER_02",
            local_name=None,
            name="Jordan",
            global_speaker_id=None,
            global_speaker=None,
            merged_into_id=None,
        ),
        SimpleNamespace(
            diarization_label="SPEAKER_03",
            local_name=None,
            name="Speaker 4",
            global_speaker_id=99,
            global_speaker=SimpleNamespace(name="Priya"),
            merged_into_id=None,
        ),
        SimpleNamespace(
            diarization_label="SPEAKER_04",
            local_name=None,
            name="Unknown",
            global_speaker_id=None,
            global_speaker=None,
            merged_into_id=2,
        ),
    ]

    labels = get_speakers_eligible_for_llm_renaming(speakers)

    assert labels == ("SPEAKER_00",)


def test_build_automatic_meeting_intelligence_request_uses_eligible_speakers() -> None:
    speakers = [
        SimpleNamespace(
            diarization_label="SPEAKER_00",
            local_name=None,
            name="Speaker 1",
            global_speaker_id=None,
            global_speaker=None,
            merged_into_id=None,
        ),
        SimpleNamespace(
            diarization_label="SPEAKER_01",
            local_name=None,
            name="Alex",
            global_speaker_id=None,
            global_speaker=None,
            merged_into_id=None,
        ),
    ]

    request = build_automatic_meeting_intelligence_request(
        "[00:00 - 00:05] Speaker 1: Status update.",
        speakers,
        user_notes="Confirm the rollout date",
    )

    assert request.unresolved_speakers == ("SPEAKER_00",)
    assert request.user_notes == "Confirm the rollout date"


def test_finalise_automatic_meeting_intelligence_result_appends_user_notes() -> None:
    result = AutomaticMeetingIntelligenceResult(
        speaker_mapping={"SPEAKER_00": "Alex"},
        title="Launch Readiness Review",
        notes_markdown="# Meeting Notes\n\n## Summary\nAll teams are ready.",
    )

    finalised = finalise_automatic_meeting_intelligence_result(
        result,
        "Confirm the rollout date",
    )

    assert "## User Notes" in finalised.notes_markdown
    assert "- [User] Confirm the rollout date" in finalised.notes_markdown


def test_finalise_substitutes_residual_labels_left_in_notes_by_the_model() -> None:
    # The model returned a correct mapping but (as weaker models do) left the raw
    # SPEAKER_XX labels in the prose. finalise must repair the notes body.
    result = AutomaticMeetingIntelligenceResult(
        speaker_mapping={"SPEAKER_00": "Gary", "SPEAKER_01": "Interviewer"},
        title="Wealth Tax Debate",
        notes_markdown=(
            "# Wealth Tax Debate\n\n## Detailed Notes\n"
            "- **SPEAKER_00** argues for an exit tax.\n"
            "- **SPEAKER_01** challenges the numbers, and SPEAKER_00 responds."
        ),
    )

    finalised = finalise_automatic_meeting_intelligence_result(result, None)

    assert "SPEAKER_00" not in finalised.notes_markdown
    assert "SPEAKER_01" not in finalised.notes_markdown
    assert "**Gary** argues for an exit tax." in finalised.notes_markdown
    assert "**Interviewer** challenges the numbers, and Gary responds." in (
        finalised.notes_markdown
    )


def test_finalise_leaves_unmapped_labels_untouched() -> None:
    # A label the model was not confident about is omitted from the mapping; the
    # generic label must remain in the notes (prompt contract), not be blanked.
    result = AutomaticMeetingIntelligenceResult(
        speaker_mapping={"SPEAKER_00": "Gary"},
        title="Partial Attribution",
        notes_markdown=(
            "# Partial Attribution\n\n## Notes\n"
            "- SPEAKER_00 spoke first; SPEAKER_01 remained unidentified."
        ),
    )

    finalised = finalise_automatic_meeting_intelligence_result(result, None)

    assert "Gary spoke first" in finalised.notes_markdown
    assert "SPEAKER_01 remained unidentified" in finalised.notes_markdown


def test_finalise_label_substitution_avoids_partial_prefix_collisions() -> None:
    # SPEAKER_1 must not clobber SPEAKER_10; whole-word, longest-first matching.
    result = AutomaticMeetingIntelligenceResult(
        speaker_mapping={"SPEAKER_1": "Ana", "SPEAKER_10": "Bruno"},
        title="Collision Guard",
        notes_markdown="# Collision Guard\n\n## Notes\nSPEAKER_10 and SPEAKER_1 spoke.",
    )

    finalised = finalise_automatic_meeting_intelligence_result(result, None)

    assert "Bruno and Ana spoke." in finalised.notes_markdown


def test_resolve_recording_speaker_name_prefers_local_then_global_then_name() -> None:
    speaker = SimpleNamespace(
        diarization_label="SPEAKER_00",
        local_name="Local Name",
        global_speaker=SimpleNamespace(name="Global Name"),
        name="Fallback Name",
    )

    assert resolve_recording_speaker_name(speaker) == "Local Name"


def test_is_placeholder_speaker_name_detects_generic_names() -> None:
    assert is_placeholder_speaker_name("SPEAKER_00") is True
    assert is_placeholder_speaker_name("Speaker 3") is True
    assert is_placeholder_speaker_name("Unknown") is True
    assert is_placeholder_speaker_name("Alex") is False
