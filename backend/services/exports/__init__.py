"""Meeting exports: notes, analytics and transcript as TXT, PDF or DOCX.

``builder`` turns a meeting into an ``ExportDocument``; one renderer per format
turns that into bytes. See ``model.py`` for why the two are kept apart.
"""

from __future__ import annotations

from typing import Literal

from .builder import SECTION_ORDER, MeetingParts, build_document, ordered_sections
from .model import ExportDocument

ExportFormat = Literal["txt", "pdf", "docx"]

MEDIA_TYPES: dict[str, str] = {
    "txt": "text/plain; charset=utf-8",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def render(document: ExportDocument, export_format: ExportFormat) -> bytes:
    """The document in one format. Blocking: run it off the event loop."""
    if export_format == "pdf":
        from .render_pdf import render_pdf

        return render_pdf(document)
    if export_format == "docx":
        from .render_docx import render_docx

        return render_docx(document)
    from .render_txt import render_txt

    return render_txt(document).encode("utf-8")


__all__ = [
    "MEDIA_TYPES",
    "SECTION_ORDER",
    "ExportDocument",
    "ExportFormat",
    "MeetingParts",
    "build_document",
    "ordered_sections",
    "render",
]
