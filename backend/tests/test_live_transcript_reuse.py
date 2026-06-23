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


def test_build_transcription_result_from_segments_preserves_live_word_timestamps():
    result, segments = build_transcription_result_from_segments(
        [
            {
                "start": 10.0,
                "end": 11.5,
                "speaker": "LIVE_01",
                "text": "Hello there",
                "confidence_payload": {
                    "asr_word_timestamps_available": True,
                    "asr_segments": [
                        {
                            "start_ms": 10000,
                            "end_ms": 11500,
                            "text": "Hello there",
                            "words": [
                                {"start_ms": 10020, "end_ms": 10350, "word": "Hello"},
                                {"start_ms": 10400, "end_ms": 10900, "word": "there"},
                            ],
                        }
                    ],
                },
            }
        ]
    )

    assert result == {
        "text": "Hello there",
        "segments": [
            {
                "start": 10.0,
                "end": 11.5,
                "text": "Hello there",
                "words": [
                    {"start": 10.02, "end": 10.35, "word": "Hello"},
                    {"start": 10.4, "end": 10.9, "word": "there"},
                ],
            }
        ],
    }
    assert segments == result["segments"]


def test_build_transcription_result_from_segments_preserves_live_ids():
    result, segments = build_transcription_result_from_segments(
        [
            {
                "id": "live-utt-1",
                "start": 10.0,
                "end": 11.5,
                "speaker": "LIVE_01",
                "text": "Hello there",
                "confidence_payload": {
                    "asr_segments": [
                        {
                            "start_ms": 10000,
                            "end_ms": 11500,
                            "text": "Hello there",
                            "words": [
                                {"start_ms": 10020, "end_ms": 10350, "word": "Hello"},
                                {"start_ms": 10400, "end_ms": 10900, "word": "there"},
                            ],
                        }
                    ],
                },
            }
        ]
    )

    assert result == {
        "text": "Hello there",
        "segments": [
            {
                "id": "live-utt-1",
                "start": 10.0,
                "end": 11.5,
                "text": "Hello there",
                "words": [
                    {
                        "start": 10.02,
                        "end": 10.35,
                        "word": "Hello",
                        "source_public_id": "live-utt-1",
                    },
                    {
                        "start": 10.4,
                        "end": 10.9,
                        "word": "there",
                        "source_public_id": "live-utt-1",
                    },
                ],
            }
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


def test_apply_live_authority_preserves_clear_microphone_source_speaker():
    live_segments = [
        {
            "start": 0,
            "end": 2,
            "speaker": "LIVE_01",
            "speaker_confidence": 0.82,
            "text": "local speaker",
            "confidence_payload": {
                "source_channel_activity": {
                    "dominant_source": "microphone",
                    "source_overlap": False,
                }
            },
        }
    ]
    combined_segments = [
        {
            "start": 0,
            "end": 2,
            "speaker": "SPEAKER_00",
            "overlapping_speakers": ["SPEAKER_01"],
            "text": "local speaker",
        }
    ]

    result = apply_live_authority_to_segments(live_segments, combined_segments)

    assert result[0]["speaker"] == "LIVE_01"
    assert result[0]["overlapping_speakers"] == []
    assert result[0]["speaker_state"] == "stable"
    assert result[0]["speaker_state_source"] == "source_channel"


def test_apply_live_authority_does_not_use_array_index_for_merged_final_segment():
    live_segments = [
        {"id": "live-1", "start": 0, "end": 1, "speaker": "LIVE_01", "text": "first"},
        {"id": "live-2", "start": 1, "end": 2, "speaker": "LIVE_02", "text": "second"},
    ]
    combined_segments = [
        {"start": 0, "end": 2, "speaker": "SPEAKER_00", "text": "final merged"},
    ]

    result = apply_live_authority_to_segments(live_segments, combined_segments)

    assert result[0]["text"] == "final merged"
    assert result[0]["speaker"] == "SPEAKER_00"
    assert result[0]["live_reuse_alignment"]["status"] == "rejected"
    assert result[0]["live_reuse_alignment"]["matched_live_utterance_ids"] == []
    assert result[0]["live_reuse_alignment"]["candidate_live_utterance_ids"] == [
        "live-1"
    ]


def test_apply_live_authority_rejects_even_split_from_one_live_segment():
    live_segments = [
        {
            "id": "live-1",
            "start": 0,
            "end": 2,
            "speaker": "LIVE_01",
            "text": "hello world",
        },
    ]
    combined_segments = [
        {"start": 0, "end": 1, "speaker": "SPEAKER_00", "text": "hello"},
        {"start": 1, "end": 2, "speaker": "SPEAKER_01", "text": "world"},
    ]

    result = apply_live_authority_to_segments(live_segments, combined_segments)

    assert result[0]["text"] == "hello"
    assert result[0]["speaker"] == "SPEAKER_00"
    assert result[0]["live_reuse_alignment"]["status"] == "rejected"
    assert result[1]["text"] == "world"
    assert result[1]["speaker"] == "SPEAKER_01"
    assert result[1]["live_reuse_alignment"]["status"] == "rejected"


def test_apply_live_authority_preserves_manual_text_only_when_alignment_is_safe():
    safe_result = apply_live_authority_to_segments(
        [
            {
                "id": "live-1",
                "start": 0,
                "end": 2,
                "speaker": "LIVE_01",
                "text": "manual merged text",
                "text_manually_edited": True,
            }
        ],
        [{"start": 0, "end": 2, "speaker": "SPEAKER_00", "text": "final text"}],
    )
    unsafe_result = apply_live_authority_to_segments(
        [
            {
                "id": "live-1",
                "start": 0,
                "end": 1,
                "speaker": "LIVE_01",
                "text": "manual first",
                "text_manually_edited": True,
            },
            {
                "id": "live-2",
                "start": 1,
                "end": 2,
                "speaker": "LIVE_02",
                "text": "second",
            },
        ],
        [{"start": 0, "end": 2, "speaker": "SPEAKER_00", "text": "final merged"}],
    )

    assert safe_result[0]["text"] == "manual merged text"
    assert safe_result[0]["text_manually_edited"] is True
    assert safe_result[0]["live_reuse_alignment"]["manual_override_reasons"] == [
        "manual_text_locked"
    ]
    assert unsafe_result[0]["text"] == "final merged"
    assert "text_manually_edited" not in unsafe_result[0]
    assert unsafe_result[0]["live_reuse_alignment"]["status"] == "rejected"


def test_apply_live_authority_transfers_microphone_source_only_on_matched_span():
    live_segments = [
        {
            "id": "live-1",
            "start": 0,
            "end": 1,
            "speaker": "LIVE_01",
            "speaker_confidence": 0.82,
            "text": "local speaker",
            "confidence_payload": {
                "source_channel_activity": {
                    "dominant_source": "microphone",
                    "source_overlap": False,
                }
            },
        }
    ]

    matched_result = apply_live_authority_to_segments(
        live_segments,
        [{"start": 0, "end": 1, "speaker": "SPEAKER_00", "text": "local speaker"}],
    )
    unmatched_result = apply_live_authority_to_segments(
        live_segments,
        [{"start": 3, "end": 4, "speaker": "SPEAKER_00", "text": "other speaker"}],
    )

    assert matched_result[0]["speaker"] == "LIVE_01"
    assert matched_result[0]["speaker_state_source"] == "source_channel"
    assert unmatched_result[0]["speaker"] == "SPEAKER_00"
    assert unmatched_result[0]["live_reuse_alignment"]["status"] == "rejected"


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


def test_map_final_speakers_to_live_labels_rejects_ambiguous_majority():
    mapping = map_final_speakers_to_live_labels(
        [
            {"speaker": "LIVE_01", "start": 0, "end": 1, "text": "a"},
            {"speaker": "LIVE_02", "start": 1, "end": 2, "text": "b"},
        ],
        [
            {"speaker": "SPEAKER_00", "start": 0, "end": 2, "text": "a b"},
        ],
    )

    assert mapping == {}


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
