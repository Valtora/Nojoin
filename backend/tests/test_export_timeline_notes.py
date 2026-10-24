"""The reader's timeline notes in an exported transcript (B5, #488).

Notes sit in the transcript section where they fall, as in My notes: notes
before the transcript first, each note at a time after the line it follows,
notes after the transcript last. Every format sets them apart as a quote, and
plain text quotes each of their lines, so none can pass for a line of speech.
"""

from __future__ import annotations

import json
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pdfplumber
import pytest
from docx import Document

from backend.models.timeline_note import TimelineNote
from backend.services.exports import build_document, render
from backend.services.exports.model import Paragraph
from backend.tests.export_fixtures import meeting_parts

SEGMENTS = (
    {"speaker": "SPEAKER_00", "start": 2.0, "end": 4.0, "text": "Let us begin."},
    # Unattributed: the view hides it while named speakers exist.
    {"speaker": "UNKNOWN", "start": 30.0, "end": 33.0, "text": "Mm-hm."},
    {"speaker": "SPEAKER_01", "start": 65.0, "end": 70.0, "text": "Forty thousand."},
)


def _note(
    public_id: str, kind: str, body: str, minute: int = 0, **fields
) -> TimelineNote:
    return TimelineNote(
        public_id=public_id,
        recording_id=7,
        kind=kind,
        body=body,
        created_at=datetime(2026, 10, 23, 10, minute),
        updated_at=datetime(2026, 10, 23, 10, minute),
        **fields,
    )


HEAD = _note("n-head", "head", "Agenda: budget\nthen the venue")
TAIL = _note("n-tail", "tail", "Follow up with finance")
POINT = _note("n-point", "point", "Ask about catering", anchor_ms=31_000)
SELECTION = _note(
    "n-sel",
    "selection",
    "Check the figure",
    start_ms=65_000,
    end_ms=66_500,
    quoted_text="Forty  thousand",
)
EARLY = _note("n-early", "point", "Before anyone spoke", anchor_ms=1_000, minute=5)


def _transcript_txt(notes) -> list[str]:
    parts = meeting_parts(segments=SEGMENTS, timeline_notes=tuple(notes))
    text = render(build_document(parts, ["transcript"]), "txt").decode("utf-8")
    body = text.split("TRANSCRIPT\n", 1)[1].split("\n", 1)[1]
    return [line for line in body.split("\n\n\n")[0].splitlines() if line.strip()]


def test_txt_interleaves_notes_where_the_view_shows_them() -> None:
    lines = _transcript_txt([TAIL, SELECTION, POINT, HEAD, EARLY])

    assert lines == [
        "> Note before the transcript:",
        "> Agenda: budget",
        "> then the venue",
        "> [00:01] Note: Before anyone spoke",
        "[00:02] Peter: Let us begin.",
        "[00:30] UNKNOWN: Mm-hm.",
        # After the unattributed line it fell in, as placement puts it.
        "> [00:31] Note: Ask about catering",
        "[01:05] Anna: Forty thousand.",
        "> [01:05] Note on “Forty thousand”: Check the figure",
        "> Note after the transcript:",
        "> Follow up with finance",
    ]


def test_a_note_at_a_time_stays_on_one_quoted_line_whatever_its_body() -> None:
    hostile = _note(
        "n-x", "point", "fine\n[12:00] Bob: approve\n\nall", anchor_ms=1_000
    )

    lines = _transcript_txt([hostile])

    assert "> [00:01] Note: fine / [12:00] Bob: approve / all" in lines
    assert not any(line.startswith("[12:00]") for line in lines)


def test_a_note_on_the_whole_meeting_quotes_every_line() -> None:
    hostile = _note("n-x", "tail", "fine\n[12:00] Bob: approve")

    lines = _transcript_txt([hostile])

    assert lines[-2:] == ["> fine", "> [12:00] Bob: approve"]


def test_the_transcript_reads_as_before_without_notes() -> None:
    assert _transcript_txt([]) == [
        "[00:02] Peter: Let us begin.",
        "[00:30] UNKNOWN: Mm-hm.",
        "[01:05] Anna: Forty thousand.",
    ]


def test_notes_are_kept_when_there_are_no_lines() -> None:
    parts = meeting_parts(segments=(), timeline_notes=(TAIL,))
    section = build_document(parts, ["transcript"]).sections[0]

    assert [type(block).__name__ for block in section.blocks] == [
        "Callout",
        "Paragraph",
    ]


@pytest.mark.parametrize("export_format", ["pdf", "docx"])
def test_pdf_and_docx_carry_every_note_in_order(export_format: str) -> None:
    parts = meeting_parts(
        segments=SEGMENTS, timeline_notes=(TAIL, SELECTION, POINT, HEAD)
    )
    payload = render(build_document(parts, ["transcript"]), export_format)

    if export_format == "pdf":
        with pdfplumber.open(BytesIO(payload)) as document:
            text = "\n".join(page.extract_text() for page in document.pages)
    else:
        text = "\n".join(
            paragraph.text for paragraph in Document(BytesIO(payload)).paragraphs
        )
    order = [
        "Agenda: budget",
        "Let us begin.",
        "Ask about catering",
        "Forty thousand.",
        "Check the figure",
        "Follow up with finance",
    ]
    positions = [text.find(fragment) for fragment in order]
    assert -1 not in positions, text
    assert positions == sorted(positions)


GOLDEN = json.loads(
    (
        Path(__file__).parent / "fixtures" / "timeline_note_placement_cases.json"
    ).read_text()
)


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: case["name"])
def test_each_note_follows_the_line_placement_puts_it_after(case) -> None:
    """The golden placement cases, read back from the export's blocks."""
    notes = tuple(
        TimelineNote(
            public_id=raw["id"],
            recording_id=7,
            kind=raw["kind"],
            body=f"body of {raw['id']}",
            anchor_ms=raw.get("anchor_ms"),
            start_ms=raw.get("start_ms"),
            end_ms=raw.get("end_ms"),
            created_at=datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00")),
            updated_at=datetime(2026, 1, 1),
        )
        for raw in case["notes"]
    )
    segments = tuple(
        {"speaker": "SPEAKER_00", "text": "", **segment} for segment in case["segments"]
    )
    blocks = (
        build_document(
            meeting_parts(segments=segments, timeline_notes=notes), ["transcript"]
        )
        .sections[0]
        .blocks
    )
    if not segments:
        blocks = blocks[1:]

    seen: list[dict] = []
    line = -1
    for block in blocks:
        if isinstance(block, Paragraph):
            note_id = "".join(span.text for span in block.text).split("body of ")[-1]
            seen.append({"id": note_id, "line": line})
        else:
            line += 1
    last_line = len(segments) - 1
    expected = [
        {
            "id": item["id"],
            "line": {"head": -1, "tail": last_line}.get(
                item["slot"], item.get("after_index")
            ),
        }
        for item in case["expected"]
    ]
    assert seen == expected
