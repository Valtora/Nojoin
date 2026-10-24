"""Files attached to timeline notes, and the switch that lets the AI read them.

A note may hold one file: a still of the shared screen taken during the
meeting, or any file a document may be, pasted or dropped into the note box.
It is a ``documents`` row with ``note_id`` set, so it is stored, archived and
deleted as a document is, and takes its time from its note. Unlike a document
attached to the meeting, it is parsed, and read by notes generation, Meeting
Chat and search, only while its ``use_in_notes`` switch is on.

The images are drawn small for the Captures tab and the note's line, and at
print size for PDF and Word exports. Pillow is imported inside those calls:
the API imports this module on startup, and the workers read the switch rule.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import and_, or_
from sqlmodel import col, select

from backend.models.document import Document

# The attachments drawn as pictures, by extension, as the parse path tells
# images (backend.processing.documents.formats).
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})

# Past this, an image is not decoded: an 8K screen is 33 million pixels.
MAX_IMAGE_PIXELS = 64_000_000

# The Captures tab and a note's line show a preview at most this wide or tall.
PREVIEW_MAX_EDGE_PX = 640

# A page of a PDF or Word export is about 6.7 inches across, so this is about
# 240 dpi over its full width: sharp in print, small in the file.
EXPORT_MAX_EDGE_PX = 1600


def feeds_notes(document: Document) -> bool:
    """Whether the AI reads this document: any document, an attachment when on."""
    return document.note_id is None or bool(document.use_in_notes)


def feeds_notes_clause():
    """``feeds_notes`` as a WHERE clause on documents."""
    return or_(col(Document.note_id).is_(None), col(Document.use_in_notes).is_(True))


def unread_attachment_ids():
    """The ids of attachments switched off, whose chunks search leaves out."""
    return select(Document.id).where(
        and_(col(Document.note_id).is_not(None), col(Document.use_in_notes).is_(False))
    )


def is_image(document: Document) -> bool:
    return os.path.splitext(document.file_path or "")[1].lower() in IMAGE_EXTENSIONS


class ImageUnreadable(ValueError):
    """The file could not be drawn. The message is for the user."""


@dataclass(frozen=True)
class RenderedImage:
    data: bytes
    width_px: int
    height_px: int


def render_image(path: str, max_edge_px: int, *, quality: int = 82) -> RenderedImage:
    """The image scaled to fit ``max_edge_px``, as a JPEG on white.

    EXIF rotation is applied and transparency laid over white, so every copy
    reads as the image does on screen. An image already small enough is
    re-encoded at its own size rather than enlarged.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(path) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ImageUnreadable("This image is too large to draw.")
            source.draft("RGB", (max_edge_px, max_edge_px))
            image = ImageOps.exif_transpose(source)
            image.thumbnail((max_edge_px, max_edge_px), Image.Resampling.LANCZOS)
            if image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            ):
                image = image.convert("RGBA")
                flat = Image.new("RGB", image.size, (255, 255, 255))
                flat.paste(image, mask=image.getchannel("A"))
                image = flat
            elif image.mode != "RGB":
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            return RenderedImage(buffer.getvalue(), image.width, image.height)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError):
        raise ImageUnreadable("This image could not be read.") from None


def render_for_export(path: str) -> Optional[RenderedImage]:
    """The image at print size, or None when its file is gone or unreadable."""
    try:
        return render_image(path, EXPORT_MAX_EDGE_PX, quality=85)
    except ImageUnreadable:
        return None
