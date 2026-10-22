"""Export-path coverage for tables in meeting notes (issues #136 and #419).

Exercises whole documents rather than a renderer alone, so a regression in how
notes reach a format is caught as well.
"""

from io import BytesIO

import pymupdf
from docx import Document

from backend.services.exports import build_document, render, theme
from backend.tests.export_fixtures import meeting_parts


def _pdf(parts, sections=("notes",), *, branded=True):
    payload = render(build_document(parts, sections, branded=branded), "pdf")
    return pymupdf.open(stream=payload, filetype="pdf")


def _header_fills(page) -> list:
    """Filled shapes in the table header colour on one page."""
    target = tuple(int(theme.TABLE_HEADER_FILL[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return [
        drawing
        for drawing in page.get_drawings()
        if drawing.get("fill") is not None
        and all(abs(a - b) < 0.01 for a, b in zip(drawing["fill"], target))
    ]


def test_docx_export_writes_native_tables() -> None:
    payload = render(build_document(meeting_parts(), ["notes"]), "docx")
    document = Document(BytesIO(payload))

    assert len(document.tables) == 2
    assert len(document.tables[1].columns) == 6
    assert document.tables[1].rows[1].cells[5].text == "ENG-1421"


def test_docx_export_keeps_the_transcript_alongside_tables() -> None:
    payload = render(build_document(meeting_parts(), ["notes", "transcript"]), "docx")
    document = Document(BytesIO(payload))

    body_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Let us begin." in body_text
    assert "[00:00]\tPeter: " in body_text
    assert len(document.tables) == 2


def test_pdf_export_draws_tables_without_markdown_delimiters() -> None:
    with _pdf(meeting_parts()) as document:
        page = document[0]
        text = page.get_text()
        assert len(page.get_drawings()) > 0

    assert "ENG-1421" in text
    # Raw Markdown delimiters must not survive into the rendered page.
    assert "| --- |" not in text


def test_pdf_header_backgrounds_stay_on_the_page_with_the_table() -> None:
    # Issue #419: markdown-pdf repainted every table header row's background at
    # the same position on each later page, over whatever text was there.
    bullets = "\n".join(
        f"- Discussion point {i} with enough words to fill a line." for i in range(140)
    )
    parts = meeting_parts(
        notes=f"{meeting_parts().notes}\n## Discussion\n\n{bullets}\n"
    )

    with _pdf(parts) as document:
        assert document.page_count >= 3
        assert len(_header_fills(document[0])) == 2
        for page in list(document)[1:]:
            assert _header_fills(page) == [], (
                f"stray header fill on page {page.number + 1}"
            )


def test_pdf_header_row_repeats_only_while_its_table_continues() -> None:
    rows = "\n".join(f"| ACT-{i:03d} | Task {i} | Owner {i} |" for i in range(120))
    table = f"| ID | Action | Owner |\n| --- | --- | --- |\n{rows}\n"
    parts = meeting_parts(notes=f"## Actions\n\n{table}\n## After\n\nClosing words.\n")

    with _pdf(parts) as document:
        pages_with_rows = [p for p in document if "ACT-" in p.get_text()]
        assert len(pages_with_rows) >= 2
        # Every page the table runs onto opens with its header row again.
        for page in pages_with_rows:
            assert len(_header_fills(page)) == 1
            assert "Owner" in page.get_text()


def test_pdf_export_handles_notes_without_tables() -> None:
    with _pdf(meeting_parts(notes="## Summary\n\nA short summary.")) as document:
        assert "A short summary." in document[0].get_text()
        assert _header_fills(document[0]) == []


def test_txt_export_pads_notes_tables_into_columns() -> None:
    text = render(build_document(meeting_parts(), ["notes"]), "txt").decode("utf-8")

    assert "| ID      | Decision" in text
    assert (
        "| DEC-001 | Use PostgreSQL as the primary persistence layer | Architecture alignment |"
        in text
    )
