import json
from types import SimpleNamespace

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
    assert result.notes_markdown.startswith("# Meeting Notes")


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


def test_automatic_meeting_intelligence_result_requires_meeting_notes_header() -> None:
    try:
        AutomaticMeetingIntelligenceResult(
            speaker_mapping={"SPEAKER_00": "Alex"},
            title="Launch Readiness Review",
            notes_markdown="## Summary\nAll teams are ready.",
        )
    except MeetingIntelligenceContractError as exc:
        assert "# Meeting Notes" in str(exc)
    else:
        raise AssertionError("Expected MeetingIntelligenceContractError")


def test_build_automatic_meeting_intelligence_prompt_includes_shared_sections() -> None:
    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript="[00:00 - 00:05] Speaker 1: Status update.",
        unresolved_speakers=("SPEAKER_00", "SPEAKER_02"),
        user_notes="Confirm the rollout date",
        prefer_short_titles=True,
    )

    prompt = build_automatic_meeting_intelligence_prompt(request)

    assert "Only these diarization labels may appear in `speaker_mapping`:" in prompt
    assert "- SPEAKER_00" in prompt
    assert "- SPEAKER_02" in prompt
    assert "Confirm the rollout date" in prompt
    assert "3-5 words" in prompt
    assert "Return valid JSON only" in prompt


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