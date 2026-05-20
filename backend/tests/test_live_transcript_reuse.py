from backend.utils.live_transcript import (
    apply_live_authority_to_segments,
    build_transcription_result_from_segments,
    map_final_speakers_to_live_labels,
    merge_reusable_segments,
)


def test_build_transcription_result_from_segments_strips_runtime_fields():
    result, segments = build_transcription_result_from_segments(
        [
            {
                "start": 0,
                "end": 1.5,
                "speaker": "LIVE_01",
                "text": " Hello ",
                "provisional": True,
            },
            {"start": 2, "end": 3, "speaker": "LIVE_02", "text": ""},
            {"start": 3, "end": 4, "speaker": "LIVE_01", "text": "again"},
        ]
    )

    assert result == {
        "text": "Hello again",
        "segments": [
            {"start": 0.0, "end": 1.5, "text": "Hello"},
            {"start": 3.0, "end": 4.0, "text": "again"},
        ],
    }
    assert segments == result["segments"]


def test_apply_live_authority_preserves_manual_speaker_and_text_edits():
    live_segments = [
        {
            "start": 0,
            "end": 1,
            "speaker": "LIVE_01",
            "text": "Corrected text",
            "text_manually_edited": True,
        },
        {
            "start": 1,
            "end": 2,
            "speaker": "LIVE_02",
            "text": "Speaker fixed",
            "speaker_manually_edited": True,
        },
    ]
    combined_segments = [
        {"start": 0, "end": 1, "speaker": "SPEAKER_00", "text": "Original"},
        {
            "start": 1,
            "end": 2,
            "speaker": "SPEAKER_01",
            "overlapping_speakers": ["SPEAKER_00"],
            "text": "Original two",
        },
    ]

    result = apply_live_authority_to_segments(live_segments, combined_segments)

    assert result[0]["text"] == "Corrected text"
    assert result[0]["speaker"] == "SPEAKER_00"
    assert result[0]["text_manually_edited"] is True
    assert result[1]["text"] == "Speaker fixed"
    assert result[1]["speaker"] == "LIVE_02"
    assert result[1]["overlapping_speakers"] == []
    assert result[1]["speaker_manually_edited"] is True


def test_map_final_speakers_to_live_labels_uses_duration_majority():
    mapping = map_final_speakers_to_live_labels(
        [
            {"speaker": "LIVE_01", "start": 0, "end": 1, "text": "a"},
            {"speaker": "LIVE_02", "start": 1, "end": 6, "text": "b"},
            {
                "speaker": "LIVE_03",
                "start": 6,
                "end": 9,
                "text": "c",
                "speaker_manually_edited": True,
            },
        ],
        [
            {"speaker": "SPEAKER_00", "start": 0, "end": 1, "text": "a"},
            {"speaker": "SPEAKER_00", "start": 1, "end": 6, "text": "b"},
            {"speaker": "SPEAKER_01", "start": 6, "end": 9, "text": "c"},
        ],
    )

    assert mapping == {"SPEAKER_00": "LIVE_02"}


def test_merge_reusable_segments_deduplicates_overlapping_rows():
    merged = merge_reusable_segments(
        [
            {"start": 0, "end": 1, "speaker": "LIVE_01", "text": "hello"},
            {"start": 2, "end": 3, "speaker": "LIVE_02", "text": "world"},
        ],
        [
            {"start": 2, "end": 3, "speaker": "UNKNOWN", "text": "world"},
            {"start": 3, "end": 4, "speaker": "UNKNOWN", "text": "again"},
        ],
    )

    assert merged == [
        {"start": 0, "end": 1, "speaker": "LIVE_01", "text": "hello"},
        {"start": 2, "end": 3, "speaker": "LIVE_02", "text": "world"},
        {"start": 3, "end": 4, "speaker": "UNKNOWN", "text": "again"},
    ]