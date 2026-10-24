"""Checking a capture's image and drawing smaller copies of it.

A capture is a PNG or JPEG: a frame of the shared screen, which the browser
encodes, or an image pasted into the note box. The upload is checked here
before its row is written, so a file that is not one of those, or that no
reader could open, never reaches the documents list. The same images are
drawn small for the Captures list and the transcript's markers, and at print
size for PDF and Word exports.

Pillow is imported inside each function: the API imports this module on
startup, and only these calls need it.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

# The formats a capture may be, by the first bytes of the file, with the media
# type and extension the stored file takes. The extension is what tells the
# parse path the file is an image (backend.processing.documents.formats).
_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
)

# Bytes enough to tell the formats apart.
SIGNATURE_BYTES = 8

# Larger than any screen a browser shares (an 8K display is 7680 x 4320),
# small enough that decoding one cannot exhaust the API's memory.
MAX_CAPTURE_EDGE_PX = 16_384
MAX_CAPTURE_PIXELS = 64_000_000

# The Captures list and the transcript's markers draw a preview at most this
# wide or tall, twice the largest size they show it at.
PREVIEW_MAX_EDGE_PX = 640

# A page of a PDF or Word export is about 6.7 inches across, so this is about
# 240 dpi over its full width: sharp in print, small in the file.
EXPORT_MAX_EDGE_PX = 1600


class CaptureImageError(ValueError):
    """The upload is not an image a capture can be. The message is for the user."""


@dataclass(frozen=True)
class CaptureImageFormat:
    media_type: str
    extension: str


@dataclass(frozen=True)
class RenderedImage:
    data: bytes
    width_px: int
    height_px: int


def sniff_capture_format(head: bytes) -> CaptureImageFormat:
    """The format a file's first bytes declare, or CaptureImageError."""
    for signature, media_type, extension in _SIGNATURES:
        if head.startswith(signature):
            return CaptureImageFormat(media_type=media_type, extension=extension)
    raise CaptureImageError("A capture must be a PNG or JPEG image.")


def check_capture_image(path: str) -> tuple[int, int]:
    """The image's width and height, once Pillow has read and verified it.

    Refuses a file Pillow cannot read, one whose format is not the PNG or JPEG
    its first bytes claim, and one too large to decode safely.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            width, height = image.size
            if image.format not in {"PNG", "JPEG"}:
                raise CaptureImageError("A capture must be a PNG or JPEG image.")
            if (
                max(width, height) > MAX_CAPTURE_EDGE_PX
                or width * height > MAX_CAPTURE_PIXELS
            ):
                raise CaptureImageError(
                    "This image is too large to capture: at most "
                    f"{MAX_CAPTURE_EDGE_PX} pixels a side."
                )
            image.verify()
    except CaptureImageError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError):
        raise CaptureImageError("This image could not be read.") from None
    if width < 1 or height < 1:
        raise CaptureImageError("This image could not be read.")
    return width, height


def render_image(path: str, max_edge_px: int, *, quality: int = 82) -> RenderedImage:
    """The image scaled to fit ``max_edge_px``, as a JPEG on white.

    A photo's EXIF rotation is applied, and transparency is laid over white, so
    every copy reads as the image does on screen. An image already small enough
    is re-encoded at its own size rather than enlarged. A file Pillow cannot
    read, or one too large to decode safely, raises CaptureImageError.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        return _render(path, max_edge_px, quality)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError):
        raise CaptureImageError("This image could not be read.") from None


def _render(path: str, max_edge_px: int, quality: int) -> RenderedImage:
    from PIL import Image, ImageOps

    with Image.open(path) as source:
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
        return RenderedImage(
            data=buffer.getvalue(), width_px=image.width, height_px=image.height
        )


def render_preview(path: str) -> bytes:
    """The small JPEG the Captures list and the markers show."""
    return render_image(path, PREVIEW_MAX_EDGE_PX, quality=80).data


def render_for_export(path: str) -> Optional[RenderedImage]:
    """The capture at print size, or None when its file is gone or unreadable."""
    try:
        return render_image(path, EXPORT_MAX_EDGE_PX, quality=85)
    except CaptureImageError:
        return None
