"""Extension-to-parser registry, and what the upload endpoint accepts."""

from __future__ import annotations

import os
from typing import Callable, Dict, FrozenSet

from .types import DocumentSource, UnsupportedDocumentError

# Whether a format's images are whole-page renders or individual figures.
# It decides both the prompt and whether the vision output replaces or
# supplements the structural text -- see documents.vision.merge_page_content.
RENDERED_PAGE_FORMATS: FrozenSet[str] = frozenset({".pdf", ".png", ".jpg", ".jpeg"})


def _pdf(path: str, *, want_images: bool) -> DocumentSource:
    from .pdf import open_pdf

    return open_pdf(path, want_images=want_images)


def _pptx(path: str, *, want_images: bool) -> DocumentSource:
    from .office import open_pptx

    return open_pptx(path, want_images=want_images)


def _docx(path: str, *, want_images: bool) -> DocumentSource:
    from .office import open_docx

    return open_docx(path, want_images=want_images)


def _xlsx(path: str, *, want_images: bool) -> DocumentSource:
    from .sheets import open_xlsx

    return open_xlsx(path, want_images=want_images)


def _csv(path: str, *, want_images: bool) -> DocumentSource:
    from .sheets import open_csv

    return open_csv(path, want_images=want_images)


def _text(path: str, *, want_images: bool) -> DocumentSource:
    from .plain import open_text

    return open_text(path, want_images=want_images)


def _image(path: str, *, want_images: bool) -> DocumentSource:
    from .plain import open_image

    return open_image(path, want_images=want_images)


# Every parser is imported lazily inside its wrapper: PyMuPDF, python-pptx,
# python-docx and openpyxl are worker-only, and importing this module must stay
# cheap enough for the API process, which only needs the extension list.
_PARSERS: Dict[str, Callable[..., DocumentSource]] = {
    ".pdf": _pdf,
    ".pptx": _pptx,
    ".docx": _docx,
    ".xlsx": _xlsx,
    ".csv": _csv,
    ".txt": _text,
    ".md": _text,
    ".png": _image,
    ".jpg": _image,
    ".jpeg": _image,
}

SUPPORTED_EXTENSIONS: FrozenSet[str] = frozenset(_PARSERS)

# Formats with no text layer at all. With no vision model reachable these
# produce nothing, so the orchestrator reports an error rather than an empty
# success -- "parsed, zero content" would read as a bug.
VISION_ONLY_EXTENSIONS: FrozenSet[str] = frozenset({".png", ".jpg", ".jpeg"})


def extension_of(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def open_document(path: str, *, want_images: bool) -> DocumentSource:
    extension = extension_of(path)
    parser = _PARSERS.get(extension)
    if parser is None:
        raise UnsupportedDocumentError(f"No parser for '{extension}' files.")
    return parser(path, want_images=want_images)


def is_rendered_page_format(path: str) -> bool:
    return extension_of(path) in RENDERED_PAGE_FORMATS


def is_vision_only_format(path: str) -> bool:
    return extension_of(path) in VISION_ONLY_EXTENSIONS
