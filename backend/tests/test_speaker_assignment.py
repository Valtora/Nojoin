from backend.utils.speaker_assignment import (
    matches_speaker_name,
    reconcile_segment_assignment,
    segment_references_label,
)


def test_reconcile_segment_assignment_updates_overlap_references():
    segments = [
        {
            "start": 0.0,
            "end": 2.0,
            "speaker": "SPEAKER_00",
            "text": "Hello",
            "overlapping_speakers": ["SPEAKER_01"],
        },
        {
            "start": 0.5,
            "end": 1.5,
            "speaker": "SPEAKER_01",
            "text": "World",
            "overlapping_speakers": ["SPEAKER_00"],
        },
    ]

    changed = reconcile_segment_assignment(
        segments,
        segment_index=0,
        old_label="SPEAKER_00",
        new_label="MANUAL_1234",
    )

    assert changed is True
    assert segments[0]["speaker"] == "MANUAL_1234"
    assert segments[0]["overlapping_speakers"] == ["SPEAKER_01"]
    assert segments[1]["overlapping_speakers"] == ["MANUAL_1234"]


def test_reconcile_segment_assignment_deduplicates_self_overlap_labels():
    segments = [
        {
            "start": 0.0,
            "end": 1.0,
            "speaker": "SPEAKER_00",
            "text": "Hello",
            "overlapping_speakers": ["SPEAKER_01", "SPEAKER_00", "SPEAKER_01"],
        }
    ]

    reconcile_segment_assignment(
        segments,
        segment_index=0,
        old_label="SPEAKER_00",
        new_label="SPEAKER_01",
    )

    assert segments[0]["speaker"] == "SPEAKER_01"
    assert segments[0]["overlapping_speakers"] == []


def test_segment_references_label_checks_primary_and_overlap_labels():
    segment = {
        "speaker": "SPEAKER_00",
        "overlapping_speakers": ["SPEAKER_01", "SPEAKER_02"],
    }

    assert segment_references_label(segment, "SPEAKER_00") is True
    assert segment_references_label(segment, "SPEAKER_02") is True
    assert segment_references_label(segment, "SPEAKER_03") is False


def test_matches_speaker_name_is_case_insensitive_and_trim_aware():
    assert matches_speaker_name(" Alice Example ", "alice example") is True
    assert matches_speaker_name("Alice Example", "Bob Example") is False
    assert matches_speaker_name(None, "Alice Example") is False