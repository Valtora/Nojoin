"""Captures in an exported transcript (#653).

Each capture sits in the transcript where a note at its time would, its
picture under a stamped caption in PDF and Word and a line saying so in plain
text. "Include my notes" governs captures too.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path

import pdfplumber
import pytest
from docx import Document as WordDocument
from PIL import Image, ImageDraw

from backend.models.timeline_note import TimelineNote
from backend.services.exports import build_document, render
from backend.services.exports.builder import ExportCapture
from backend.services.exports.model import ImageBlock, Paragraph
from backend.tests.export_fixtures import exported_text, meeting_parts
from backend.utils.capture_images import (
    EXPORT_MAX_EDGE_PX,
    render_for_export,
)

SEGMENTS = (
    {"speaker": "SPEAKER_00", "start": 2.0, "end": 4.0, "text": "Let us begin."},
    {"speaker": "SPEAKER_01", "start": 65.0, "end": 70.0, "text": "Forty thousand."},
)


def _slide(path: Path, size: tuple[int, int] = (3840, 2160)) -> Path:
    image = Image.new("RGB", size, (248, 248, 248))
    ImageDraw.Draw(image).rectangle((200, 200, 1800, 500), fill=(30, 30, 30))
    image.save(path, format="PNG")
    return path


def _capture(path: Path | None, at_ms: int, minute: int = 0) -> ExportCapture:
    return ExportCapture(
        public_id=f"capture-{at_ms}",
        anchor_ms=at_ms,
        created_at=datetime(2026, 10, 24, 9, minute),
        title="Capture at 00:30",
        image=render_for_export(str(path)) if path else None,
    )


def _note(body: str, at_ms: int) -> TimelineNote:
    return TimelineNote(
        public_id=f"note-{at_ms}",
        recording_id=7,
        kind="point",
        body=body,
        anchor_ms=at_ms,
        created_at=datetime(2026, 10, 24, 9, 0),
        updated_at=datetime(2026, 10, 24, 9, 0),
    )


def _transcript_blocks(**overrides):
    document = build_document(
        meeting_parts(segments=SEGMENTS, **overrides), ["transcript"]
    )
    (section,) = document.sections
    return section.blocks


def test_a_capture_is_scaled_for_print(tmp_path: Path) -> None:
    rendered = render_for_export(str(_slide(tmp_path / "frame.png")))

    assert rendered is not None
    assert (rendered.width_px, rendered.height_px) == (EXPORT_MAX_EDGE_PX, 900)
    with Image.open(io.BytesIO(rendered.data)) as image:
        assert image.format == "JPEG"


def test_captures_and_notes_interleave_by_time(tmp_path: Path) -> None:
    frame = _slide(tmp_path / "frame.png")
    blocks = _transcript_blocks(
        captures=(_capture(frame, 66_000), _capture(frame, 30_000)),
        timeline_notes=(_note("Ask about catering", 31_000),),
    )

    kinds = [
        "line"
        if not isinstance(block, (ImageBlock, Paragraph))
        else type(block).__name__
        for block in blocks
    ]
    assert kinds == ["line", "ImageBlock", "Paragraph", "line", "ImageBlock"]
    first = blocks[1]
    assert isinstance(first, ImageBlock)
    assert "".join(span.text for span in first.caption) == "[00:30] Capture"
    assert first.alt_text == "Capture at 00:30"


def test_plain_text_says_where_the_picture_was(tmp_path: Path) -> None:
    parts = meeting_parts(
        segments=SEGMENTS, captures=(_capture(_slide(tmp_path / "f.png"), 30_000),)
    )
    text = render(build_document(parts, ["transcript"]), "txt").decode()

    assert (
        "[00:02] Peter: Let us begin.\n\n"
        "> [00:30] Capture: an image, in the PDF and Word exports\n\n"
        "[01:05] Anna: Forty thousand."
    ) in text


def test_pdf_and_word_carry_the_picture(tmp_path: Path) -> None:
    parts = meeting_parts(
        segments=SEGMENTS, captures=(_capture(_slide(tmp_path / "f.png"), 30_000),)
    )
    document = build_document(parts, ["transcript"])

    pdf = render(document, "pdf")
    with pdfplumber.open(io.BytesIO(pdf)) as opened:
        images = [image for page in opened.pages for image in page.images]
        text = "\n".join(page.extract_text() for page in opened.pages)
    assert len(images) >= 1
    assert "[00:30] Capture" in text

    docx = render(document, "docx")
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
        body = archive.read("word/document.xml").decode()
    assert any(name.endswith((".jpeg", ".jpg")) for name in media)
    assert 'descr="Capture at 00:30"' in body
    # A hairline round the picture, so a white slide keeps its edge.
    assert '<a:ln w="7620"><a:solidFill><a:srgbClr val="CBD5E1"/>' in body
    paragraphs = [p.text for p in WordDocument(io.BytesIO(docx)).paragraphs]
    assert "[00:30] Capture" in paragraphs


@pytest.mark.parametrize("export_format", ["txt", "pdf", "docx"])
def test_leaving_my_notes_out_leaves_the_captures_out(
    tmp_path: Path, export_format: str
) -> None:
    parts = meeting_parts(
        segments=SEGMENTS,
        captures=(_capture(_slide(tmp_path / "f.png"), 30_000),),
        include_user_notes=False,
    )
    content = render(build_document(parts, ["transcript"]), export_format)

    assert "Capture" not in exported_text(content, export_format)


def test_a_capture_whose_file_is_gone_says_so(tmp_path: Path) -> None:
    blocks = _transcript_blocks(captures=(_capture(None, 30_000),))

    (stand_in,) = [block for block in blocks if isinstance(block, Paragraph)]
    assert "".join(span.text for span in stand_in.text) == (
        "[00:30] Capture: the image could not be read"
    )
    assert render_for_export(str(tmp_path / "missing.png")) is None
