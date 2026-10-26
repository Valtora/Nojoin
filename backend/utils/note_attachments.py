"""Files attached to timeline notes, and the switch that lets the AI read them.

A note may hold one file of any type: a still of the shared screen taken
during the meeting, or a file pasted or dropped into the note box. It is a
``documents`` row with ``note_id`` set, so it is stored, archived and deleted
as a document is, and takes its time from its note. Unlike a document
attached to the meeting, it is parsed, and read by notes generation, Meeting
Chat and search, only while its ``use_in_notes`` switch is on, and only a
format the parser reads (``is_readable``) can be switched on. Every other file
is kept to download: served as ``application/octet-stream``, never previewed,
rendered, run or unpacked.

The images are drawn small for the Captures tab and the note's line, and at
print size for PDF and Word exports. Pillow is imported inside those calls:
the API imports this module on startup, and the workers read the switch rule.
"""

from __future__ import annotations

import io
import mimetypes
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import and_, or_
from sqlmodel import col, select

from backend.models.document import Document
from backend.models.timeline_note import TimelineNote, TimelineNoteKind
from backend.processing.documents import SUPPORTED_EXTENSIONS

# The text of the point note a screen capture makes, as the browser writes it
# (CAPTURE_NOTE_BODY in frontend/src/lib/captures/useCaptureActions.ts).
CAPTURE_NOTE_BODY = "Screen capture"

# The attachments drawn as pictures, by extension, as the parse path tells
# images (backend.processing.documents.formats).
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})

# Markup a browser would run or draw: never readable, whatever a parser takes.
UNSAFE_EXTENSIONS = frozenset(
    {".svg", ".svgz", ".html", ".htm", ".xhtml", ".xht", ".xml", ".mht", ".mhtml"}
)

_STORED_EXTENSION = re.compile(r"\.[a-z0-9]{1,10}")
TITLE_MAX_CHARS = 200

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


def note_goes_with_its_file(note: TimelineNote, file_title: str) -> bool:
    """Whether removing the note's file removes the note too (#734).

    Only a note whose text is still what the browser gave it with its file: a
    capture's point note reading exactly "Screen capture", or a note of any
    kind reading exactly the file's name, as one pasted or dropped with
    nothing typed does. No column marks either, so the text decides. Both
    sides of the second are the browser's file name, trimmed by the API, so
    the comparison is exact: case, extension and spaces included. Any other
    note, a caption typed over either text included, stays as a text note.
    """
    if note.body == file_title:
        return True
    return note.kind == TimelineNoteKind.POINT.value and note.body == CAPTURE_NOTE_BODY


def _extension(path: str | None) -> str:
    return os.path.splitext(path or "")[1].lower()


def is_image(document: Document) -> bool:
    return _extension(document.file_path) in IMAGE_EXTENSIONS


def is_readable(document: Document) -> bool:
    """Whether the parser reads this file's format, so it can be used in notes."""
    extension = _extension(document.file_path)
    return extension in SUPPORTED_EXTENSIONS and extension not in UNSAFE_EXTENSIONS


def stored_extension(filename: str | None) -> str:
    """The extension a note's file is stored under: its own if plain, else ``.bin``.

    Kept so a format becomes readable once the parser learns it, with nothing
    uploaded again; the stored name is otherwise a fresh UUID.
    """
    extension = _extension(filename)
    return extension if _STORED_EXTENSION.fullmatch(extension) else ".bin"


def media_type_for(filename: str) -> str:
    """The type recorded for a note's file: a readable format's own, else bytes."""
    extension = _extension(filename)
    if extension not in SUPPORTED_EXTENSIONS or extension in UNSAFE_EXTENSIONS:
        return "application/octet-stream"
    return mimetypes.guess_type(f"file{extension}")[0] or "application/octet-stream"


def served_media_type(document: Document) -> str:
    """What a download is served as: images as images, everything else as bytes."""
    if is_image(document):
        return mimetypes.guess_type(document.file_path)[0] or "application/octet-stream"
    return "application/octet-stream"


def clean_title(filename: str | None) -> str:
    """A file's name as shown and downloaded: no folders or control characters."""
    name = os.path.basename((filename or "").replace("\\", "/"))
    name = "".join(
        character for character in name if unicodedata.category(character)[0] != "C"
    ).strip()
    return name[:TITLE_MAX_CHARS] or "Attachment"


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
