from backend.utils.meeting_notes import append_user_notes_section, build_user_notes_prompt_section


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