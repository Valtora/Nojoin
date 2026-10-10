from backend.utils.meeting_notes import (
    append_user_notes_section,
    build_user_notes_prompt_section,
    format_segments_for_llm,
)

SEGMENTS = [
    {"start": 5.0, "end": 9.4, "speaker": "SPEAKER_00", "text": " Ship it. "},
    {
        "start": 6125.0,
        "end": 6130.0,
        "speaker": "SPEAKER_01",
        "text": "Agreed.",
        "overlapping_speakers": ["SPEAKER_00"],
    },
]
SPEAKER_MAP = {"SPEAKER_00": "Priya"}


def test_format_segments_for_llm_keeps_the_range_the_notes_prompt_uses() -> None:
    # Notes generation and Meeting Edge render with the default, so its output
    # must not move when chat asks for the shorter form.
    assert format_segments_for_llm(SEGMENTS, SPEAKER_MAP) == (
        "[00:05 - 00:09] Priya: Ship it.\n"
        "[102:05 - 102:10] SPEAKER_01 (with Priya): Agreed."
    )


def test_format_segments_for_llm_without_end_keeps_start_and_overlap() -> None:
    assert format_segments_for_llm(SEGMENTS, SPEAKER_MAP, with_end=False) == (
        "[00:05] Priya: Ship it.\n[102:05] SPEAKER_01 (with Priya): Agreed."
    )


def test_build_user_notes_prompt_section_handles_empty_notes() -> None:
    result = build_user_notes_prompt_section(None)

    assert "No user-authored notes were provided" in result


def test_append_user_notes_section_labels_each_user_note() -> None:
    notes = "# Meeting Notes\n\n## Summary\nA short summary."
    user_notes = "Follow up with finance\n- Confirm launch date"

    result = append_user_notes_section(notes, user_notes)

    assert "## User Notes" in result
    assert "- [User] Follow up with finance" in result
    assert "- [User] Confirm launch date" in result


def test_append_user_notes_section_replaces_existing_user_notes_block() -> None:
    notes = "# Meeting Notes\n\n## Summary\nA short summary.\n\n## User Notes\n- Something else"

    result = append_user_notes_section(notes, "Actual user note")

    assert result.count("## User Notes") == 1
    assert "- [User] Actual user note" in result
    assert "Something else" not in result
