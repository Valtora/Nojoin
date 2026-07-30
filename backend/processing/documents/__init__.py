"""Document parsing: structural extraction, optional visual analysis, chunking.

The public surface is deliberately small. ``formats`` answers "can this file be
parsed and how", the orchestrator in ``backend.worker.tasks.documents`` drives
the parse, and everything else here is an implementation detail of one format.

Nothing at import time pulls in PyMuPDF, python-pptx, python-docx or openpyxl:
the API process imports this package only to validate an upload's extension, and
those libraries live in the worker image alone.
"""

from .chunking import chunk_page_content
from .formats import (
    SUPPORTED_EXTENSIONS,
    VISION_ONLY_EXTENSIONS,
    extension_of,
    is_rendered_page_format,
    is_vision_only_format,
    open_document,
)
from .types import (
    DocumentSource,
    PageSource,
    UnsupportedDocumentError,
)

__all__ = [
    "DocumentSource",
    "PageSource",
    "SUPPORTED_EXTENSIONS",
    "UnsupportedDocumentError",
    "VISION_ONLY_EXTENSIONS",
    "chunk_page_content",
    "extension_of",
    "is_rendered_page_format",
    "is_vision_only_format",
    "open_document",
]
