"""Plain text, Markdown, and standalone image uploads.

Grouped because all three are single-page formats with nothing to extract
structurally -- for text the file *is* the content, and for an image there is no
text layer at all.
"""

from __future__ import annotations

import os
from typing import Iterator

from backend.utils.vision import VisionImage

from .types import DocumentSource, PageSource

_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def open_text(path: str, *, want_images: bool) -> DocumentSource:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        content = handle.read()

    def pages() -> Iterator[PageSource]:
        yield PageSource(page_number=1, title=None, text=content.strip())

    return DocumentSource(page_count=1, pages=pages)


def open_image(path: str, *, want_images: bool) -> DocumentSource:
    """A photographed whiteboard, a pasted screenshot, a scanned page.

    Unlike every other format this has no structural fallback whatsoever: with
    no vision model reachable the page is genuinely empty, which the
    orchestrator reports as an error rather than as an empty success. That
    distinction is the whole reason `want_images` is not consulted here -- the
    image is always attached, and the caller decides what to do about it.
    """
    extension = os.path.splitext(path)[1].lower()
    media_type = _IMAGE_MEDIA_TYPES.get(extension, "image/png")
    with open(path, "rb") as handle:
        data = handle.read()

    def pages() -> Iterator[PageSource]:
        yield PageSource(
            page_number=1,
            title=None,
            text="",
            images=[VisionImage(data=data, media_type=media_type, path=path)],
        )

    return DocumentSource(page_count=1, pages=pages)
