"""Every reader shows a live speaker label as "Speaker N", never as ``LIVE_PROVISIONAL_N``.

A live label has no ``RecordingSpeaker``, so the readers that resolve labels
through the recording's speakers fall back to it. Each test feeds one reader a
live-labelled line beside a named and an unnamed diarisation label.
"""

from __future__ import annotations

import backend.worker.tasks as tasks_module
from backend.api.v1.endpoints.transcripts.helpers import _format_transcript_text
from backend.services.exports.builder import build_document
from backend.services.exports.model import TranscriptLine
from backend.services.meeting_analytics.query import _describe_unlisted_key
from backend.services.meeting_analytics.warnings import build_attribution_warning
from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.tests.export_fixtures import meeting_parts
from backend.utils.live_speaker_labels import (
    live_speaker_display_name,
    speaker_display_name,
)
from backend.utils.meeting_notes import format_segments_for_llm
from backend.worker.tasks.meeting_edge_stage import (
    _build_recent_meeting_edge_transcript,
)

SEGMENTS = [
    {
        "start": 0.0,
        "end": 4.0,
        "speaker": "LIVE_PROVISIONAL_3",
        "text": "Opening remarks.",
        "overlapping_speakers": ["LIVE_PROVISIONAL_12"],
    },
    {"start": 4.0, "end": 8.0, "speaker": "SPEAKER_01", "text": "A reply."},
    {"start": 8.0, "end": 9.0, "speaker": "SPEAKER_02", "text": "Unnamed."},
]
SPEAKER_MAP = {"SPEAKER_01": "Dana"}


def test_only_a_live_label_has_a_live_display_name():
    assert live_speaker_display_name("LIVE_PROVISIONAL_3") == "Speaker 3"
    assert live_speaker_display_name("LIVE_PROVISIONAL_12") == "Speaker 12"
    # LIVE_3 is the older live label, which the pipeline makes a speaker of.
    for label in (
        "SPEAKER_03",
        "UNKNOWN",
        "LIVE_3",
        "LIVE_PROVISIONAL_",
        "LIVE_PROVISIONAL_3a",
        "xLIVE_PROVISIONAL_3",
        None,
    ):
        assert live_speaker_display_name(label) is None


def test_a_name_from_the_recording_wins_over_the_live_display_name():
    assert (
        speaker_display_name("LIVE_PROVISIONAL_3", {"LIVE_PROVISIONAL_3": "Ada"})
        == "Ada"
    )
    assert speaker_display_name("SPEAKER_02", SPEAKER_MAP) == "SPEAKER_02"


def test_llm_transcript_lines_show_live_speakers_as_speaker_n():
    rendered = format_segments_for_llm(SEGMENTS, SPEAKER_MAP)

    assert rendered.splitlines() == [
        "[00:00 - 00:04] Speaker 3 (with Speaker 12): Opening remarks.",
        "[00:04 - 00:08] Dana: A reply.",
        "[00:08 - 00:09] SPEAKER_02: Unnamed.",
    ]


def test_the_meeting_edge_window_shows_live_speakers_as_speaker_n():
    rendered = _build_recent_meeting_edge_transcript(
        SEGMENTS, SPEAKER_MAP, RecordingTimelineNotes()
    )

    assert "Speaker 3 (with Speaker 12): Opening remarks." in rendered
    assert "LIVE_" not in rendered


def test_the_meeting_intelligence_transcript_shows_live_speakers_as_speaker_n():
    rendered = tasks_module._build_automatic_meeting_intelligence_transcript(
        SEGMENTS, SPEAKER_MAP, ("SPEAKER_02",), RecordingTimelineNotes()
    )

    assert "Speaker 3 (with Speaker 12): Opening remarks." in rendered
    assert "SPEAKER_02: Unnamed." in rendered
    assert "LIVE_" not in rendered


def test_the_plain_text_transcript_shows_live_speakers_as_speaker_n():
    # The transcript download and the MCP transcript tool share this format.
    rendered = _format_transcript_text(SEGMENTS, SPEAKER_MAP)

    assert rendered.splitlines() == [
        "[00:00] Speaker 3: Opening remarks.",
        "[00:04] Dana: A reply.",
        "[00:08] SPEAKER_02: Unnamed.",
    ]


def test_document_exports_show_live_speakers_as_speaker_n():
    parts = meeting_parts(segments=tuple(SEGMENTS), speaker_names_by_label=SPEAKER_MAP)

    document = build_document(parts, ["transcript"])

    lines = [
        block
        for section in document.sections
        for block in section.blocks
        if isinstance(block, TranscriptLine)
    ]
    assert [line.speaker for line in lines] == ["Speaker 3", "Dana", "SPEAKER_02"]


def test_analytics_name_a_live_label_and_do_not_ask_to_name_it():
    live = _describe_unlisted_key("label:LIVE_PROVISIONAL_4")
    unnamed = _describe_unlisted_key("label:SPEAKER_02")

    assert live["name"] == "Speaker 4"
    assert unnamed["name"] == "SPEAKER_02"
    warning = build_attribution_warning(
        metrics={"speakers": {}}, speakers=[live, unnamed], max_speakers=None
    )
    # Only a speaker row can be named; a live label has none until processing.
    assert warning == {
        "reasons": [{"code": "unnamed_speakers", "speaker_keys": ["label:SPEAKER_02"]}]
    }
