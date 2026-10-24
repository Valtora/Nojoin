"""Shared inputs for the export tests: one meeting, with every tier measured."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

import pdfplumber
from docx import Document

from backend.services.exports import MeetingParts
from backend.utils.meeting_notes import append_user_notes_section

NOTES_WITH_TABLES = """## Key Decisions

| ID | Decision | Rationale |
| --- | --- | --- |
| DEC-001 | Use PostgreSQL as the primary persistence layer | Architecture alignment |

## Action Items / Tasks

| ID | Action | Owner | Contributors | Target date | Jira |
| --- | --- | --- | --- | --- | --- |
| ACT-001 | Prepare the architecture diagram | Peter | Anna, Jon | 2026-08-01 | ENG-1421 |
"""

# Generated notes as the notes task stores them, ending with the reader's notes.
NOTES_WITH_USER_NOTES = append_user_notes_section(
    "## Summary\nThe venue was agreed.\n\n## Action Items\n- Peter books the venue.",
    ["Agenda: budget", "00:31 Ask about catering"],
)

SPEAKERS = (
    ("k1", "SPEAKER_00", "Peter"),
    ("k2", "SPEAKER_01", "Anna"),
)


def analytics_payload(**overrides: Any) -> dict[str, Any]:
    """The dict read_recording_analytics returns, with delivery, overlap and AI measured."""
    talk = {"k1": 600_000, "k2": 400_000}
    payload: dict[str, Any] = {
        "speakers": [
            {
                "speaker_key": key,
                "name": name,
                "diarization_label": label,
                "color": None,
                "is_named": True,
            }
            for key, label, name in SPEAKERS
        ],
        "metrics": {
            "duration_ms": 1_200_000,
            "utterance_count": 120,
            "talk_time": {
                key: {"speech_ms": value, "share_of_speech": value / 1_000_000}
                for key, value in talk.items()
            },
            "turn_structure": {
                "k1": {
                    "turn_count": 30,
                    "median_turn_ms": 9_000,
                    "longest_turn_ms": 75_000,
                    "longest_turn_start_ms": 300_000,
                },
                "k2": {
                    "turn_count": 28,
                    "median_turn_ms": 6_000,
                    "longest_turn_ms": 41_000,
                    "longest_turn_start_ms": 610_000,
                },
            },
            "turn_taking": {
                "response_latency": {
                    "k1": {"median_ms": 640, "sample_count": 20, "immediate_count": 7},
                    "k2": {"median_ms": None, "sample_count": 0, "immediate_count": 3},
                },
                "transitions": [],
                "immediate_transitions": 10,
                "lapse_transitions": 2,
            },
            "timeline": {
                "buckets": [
                    {
                        "start_ms": index * 60_000,
                        "end_ms": (index + 1) * 60_000,
                        "speech_ms": {"k1": 30_000, "k2": 20_000 if index % 2 else 0},
                    }
                    for index in range(20)
                ]
            },
            "silence": {
                "speech_ms": 1_000_000,
                "silence_ms": 200_000,
                "silence_share": 0.1667,
            },
            "overlap": {"overlapping_speech_present": False},
        },
        "attribution_warning": None,
        "audio_overlap": {
            "status": "completed",
            "total_overlap_ms": 37_000,
            "overlap_share_of_audio": 0.031,
            "region_count": 2,
            "duration_ms": 1_200_000,
            "regions": [[10_000, 30_000], [600_000, 617_000]],
        },
        "audio_overlap_status": "completed",
        "audio_overlap_error_message": None,
        "delivery": {
            "cross_speaker_loudness_comparable": True,
            "speakers": {
                "k1": {
                    "words_per_minute": 182,
                    "median_f0_hz": 121,
                    "pitch_spread_semitones": 4.1,
                    "pause_count": 20,
                    "analysed_utterances": 40,
                },
                "k2": {
                    "words_per_minute": 214,
                    "median_f0_hz": 198,
                    "pitch_spread_semitones": 6.9,
                    "pause_count": 12,
                    "analysed_utterances": 30,
                },
            },
        },
        "delivery_status": "completed",
        "delivery_error_message": None,
        "delivery_stale": False,
        "delivery_baselines": {},
        "ai": {
            "transcript_truncated": False,
            "topics": [
                {
                    "title": "Database choice",
                    "start_ms": 0,
                    "end_ms": 300_000,
                    "summary": "Which database to standardise on.",
                    "led_by": "k1",
                    "leadership_basis": "proposed and closed it",
                    "contested": False,
                }
            ],
            "questions": [
                {
                    "question": "Who owns the migration?",
                    "asked_by": "k2",
                    "asked_at_ms": 400_000,
                    "answered_by": None,
                }
            ],
            "sentiment": [
                {
                    "speaker_key": "k1",
                    "tone": "positive",
                    "summary": "Backed the change.",
                    "citations": [
                        {"quote": "Postgres is the right call.", "start_ms": 60_000}
                    ],
                }
            ],
            "decisions": [
                {
                    "decision": "Use PostgreSQL",
                    "proposed_by": "k1",
                    "agreed_by": ["k2"],
                    "objected_by": [],
                    "consensus": "assumed",
                    "citations": [
                        {"quote": "Let's go with Postgres.", "start_ms": 280_000}
                    ],
                }
            ],
        },
        "ai_status": "completed",
        "ai_error_message": None,
        "ai_stale": False,
    }
    payload.update(overrides)
    return payload


def meeting_parts(**overrides: Any) -> MeetingParts:
    values: dict[str, Any] = {
        "title": "Architecture Review",
        # Naive UTC, as the schema stores it: 10:00 UTC is 11:00 BST.
        "created_at": datetime(2026, 7, 26, 10, 0),
        "timezone_name": "Europe/London",
        "duration_seconds": 1800,
        "speaker_names": ("Peter", "Anna"),
        "notes": NOTES_WITH_TABLES,
        "segments": (
            {
                "speaker": "SPEAKER_00",
                "start": 0.0,
                "end": 4.0,
                "text": "Let us begin.",
            },
            {"speaker": "SPEAKER_01", "start": 65.0, "end": 70.0, "text": "Agreed."},
        ),
        "speaker_names_by_label": {"SPEAKER_00": "Peter", "SPEAKER_01": "Anna"},
        "speaker_colors_by_label": {"SPEAKER_00": "#2b7fff", "SPEAKER_01": "#f0b100"},
        "analytics": analytics_payload(),
        "analytics_colors": {"k1": "#2b7fff", "k2": "#f0b100"},
    }
    values.update(overrides)
    return MeetingParts(**values)


def exported_text(payload: bytes, export_format: str) -> str:
    """The text a reader sees in an exported file, in reading order."""
    if export_format == "pdf":
        with pdfplumber.open(BytesIO(payload)) as document:
            return "\n".join(page.extract_text() for page in document.pages)
    if export_format == "docx":
        return "\n".join(
            paragraph.text for paragraph in Document(BytesIO(payload)).paragraphs
        )
    return payload.decode("utf-8")
