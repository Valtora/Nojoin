from backend.utils.meeting_notes import (
    append_user_notes_section,
    build_user_notes_prompt_section,
    format_segments_for_llm,
    split_user_notes_section,
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
    note_lines = ["Follow up with finance", "- Confirm launch date", "12:04 Ask Dana"]

    result = append_user_notes_section(notes, note_lines)

    # A list marker the user typed is dropped; a note's timestamp is kept.
    assert result.endswith(
        "## User Notes\n"
        "- [User] Follow up with finance\n"
        "- [User] Confirm launch date\n"
        "- [User] 12:04 Ask Dana"
    )


def test_append_user_notes_section_without_notes_drops_a_model_written_one() -> None:
    notes = "## Summary\nA short summary.\n\n## User Notes\n- [User] Invented"

    assert append_user_notes_section(notes, []) == "## Summary\nA short summary."


def test_append_user_notes_section_replaces_existing_user_notes_block() -> None:
    notes = "# Meeting Notes\n\n## Summary\nA short summary.\n\n## User Notes\n- Something else"

    result = append_user_notes_section(notes, ["Actual user note"])

    assert result.count("## User Notes") == 1
    assert "- [User] Actual user note" in result
    assert "Something else" not in result


def test_the_user_notes_section_is_found_only_as_a_heading_of_its_own() -> None:
    # Empty generated notes leave the section at the very start.
    assert split_user_notes_section("## User Notes\n- [User] Mine") == (
        "",
        "## User Notes\n- [User] Mine",
    )
    # Another heading that merely starts with the words is not the section.
    notes = "## User Notes Context\nModel text about Dana."
    assert split_user_notes_section(notes) == (notes, "")
