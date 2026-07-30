"""Shared types for document parsing.

Kept free of format-specific imports so the orchestrator and the API layer can
use them without pulling in PyMuPDF, python-pptx or openpyxl -- all of which are
worker-only dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional

from backend.utils.vision import VisionImage


class UnsupportedDocumentError(RuntimeError):
    """The file's extension has no parser registered."""


@dataclass
class PageSource:
    """One page, slide, sheet or section, before any visual analysis.

    ``text`` is what the format itself yields -- always cheap, always available,
    and the fallback whenever visual analysis is unavailable or fails.
    ``images`` are what would be sent to a vision model for this page: a
    rendered page for a PDF, the embedded pictures for a slide, the file itself
    for an image upload.
    """

    page_number: int
    title: Optional[str] = None
    text: str = ""
    images: List[VisionImage] = field(default_factory=list)


@dataclass
class DocumentSource:
    """An opened document: how many pages, and how to walk them.

    ``pages`` is a factory rather than a materialised list so a large PDF is
    rendered one page at a time. Holding several hundred rendered page images in
    memory at once would be the single largest allocation in the worker.
    """

    page_count: int
    pages: Callable[[], Iterator[PageSource]]
