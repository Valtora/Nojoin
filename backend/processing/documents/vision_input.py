"""Images fitted to every vision provider's limits, for the model call only.

An uploaded image, a still or file on a timeline note, a rendered PDF page and
a picture embedded in a slide all reach the model through here. One that is
already a small PNG or JPEG goes as it is. Anything else is scaled down to the
largest size any provider reads and re-encoded within the tightest per-image
byte limit, so a large photo or screenshot is read rather than refused. The
stored file is never touched: the copy exists only for the call.

Pillow is imported inside the functions, as the parsers import their
libraries, so importing this module stays cheap.
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING, List, Sequence

from backend.utils.vision import VisionImage

if TYPE_CHECKING:
    from PIL import Image as PILImage

logger = logging.getLogger(__name__)

# The longest edge sent to a model. Claude's high-resolution tier downscales
# past 2576 px, its standard tier past 1568, OpenAI's "high" detail past 2048,
# and Gemini tiles at 768, so a larger image costs bytes and tokens and shows
# no model anything more.
MAX_EDGE_PX = 2576

# The Claude API takes at most 2000 px a side once one request carries more
# than 20 images, which a Word file's or a slide's pictures can.
MANY_IMAGES = 20
MANY_IMAGES_MAX_EDGE_PX = 2000

# Bytes of file per image. The Claude API sets the tightest per-image limit of
# the providers and counts it in base64, a third larger than the file: 3.5 MB
# of file is about 4.7 MB of base64, under a 5 MB limit. OpenAI takes 20 MB an
# image, and Gemini 20 MB for a whole request of inline data.
MAX_IMAGE_BYTES = 3_500_000

# Past this, counted after JPEG's reduced decode, an image is not decoded: an
# 8K screen is 33 million pixels, and 64 million in RGBA is 256 MB of memory.
MAX_DECODED_PIXELS = 64_000_000

# JPEG qualities tried in turn once a PNG is too large. If none fits, the image
# shrinks by a quarter and they are tried again, down to MIN_EDGE_PX.
JPEG_QUALITIES = (90, 80, 70)
SHRINK_STEP = 0.75
MIN_EDGE_PX = 64

_MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg"}
_EXIF_ORIENTATION = 0x0112
_WHITE = (255, 255, 255)


class VisionImageTooLarge(RuntimeError):
    """The image has more pixels than Nojoin decodes for visual analysis."""


def fit_images_for_vision(images: Sequence[VisionImage]) -> List[VisionImage]:
    """Each image of one call, within every provider's limits for that call."""
    max_edge = MANY_IMAGES_MAX_EDGE_PX if len(images) > MANY_IMAGES else MAX_EDGE_PX
    return [fit_image_for_vision(image, max_edge_px=max_edge) for image in images]


def fit_image_for_vision(
    image: VisionImage,
    *,
    max_edge_px: int = MAX_EDGE_PX,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> VisionImage:
    """``image`` as a PNG or JPEG within ``max_edge_px`` and ``max_bytes``.

    EXIF rotation is applied and transparency laid over white, so the model
    sees the picture as a viewer shows it on a page. A PNG stays PNG while it
    fits, which keeps text sharp; a photo, or a PNG too large to fit, becomes
    a JPEG. Raises ``VisionImageTooLarge`` past ``MAX_DECODED_PIXELS``. Bytes
    Pillow cannot read, such as a Word file's EMF drawing, go as they are, and
    the provider decides.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(image.data)) as source:
            if _sendable_as_is(source, len(image.data), max_edge_px, max_bytes):
                media_type = _MEDIA_TYPES[source.format or ""]
                if media_type == image.media_type:
                    return image
                # Named by its extension, sent as its bytes say.
                return VisionImage(data=image.data, media_type=media_type)
            fitted = _reencode(source, max_edge_px, max_bytes)
    except Image.DecompressionBombError as exc:
        raise VisionImageTooLarge(
            "This image has too many pixels for visual analysis."
        ) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        logger.info("Image sent as uploaded: Pillow could not read it (%s).", exc)
        return image
    logger.info(
        "Image fitted for visual analysis: %s bytes of %s to %s bytes of %s.",
        len(image.data),
        image.media_type,
        len(fitted.data),
        fitted.media_type,
    )
    return fitted


def _sendable_as_is(
    source: PILImage.Image, size_bytes: int, max_edge_px: int, max_bytes: int
) -> bool:
    """Whether the file already is a PNG or JPEG every provider reads as shown."""
    if source.format not in _MEDIA_TYPES:
        return False
    if size_bytes > max_bytes or max(source.size) > max_edge_px:
        return False
    if source.getexif().get(_EXIF_ORIENTATION, 1) != 1:
        return False
    if source.format == "JPEG":
        # A CMYK print photo is converted to RGB, which every provider reads.
        return source.mode in ("L", "RGB")
    return source.mode in ("1", "L", "P", "RGB") and "transparency" not in source.info


def _reencode(source: PILImage.Image, max_edge_px: int, max_bytes: int) -> VisionImage:
    from PIL import Image, ImageOps

    was_jpeg = source.format == "JPEG"
    # A JPEG decodes at a power-of-two fraction of its size, no smaller than the
    # target, so a 100-megapixel photo costs a fraction of its memory.
    source.draft(None, _fitted_size(source.width, source.height, max_edge_px))
    if source.width * source.height > MAX_DECODED_PIXELS:
        megapixels = source.width * source.height // 1_000_000
        raise VisionImageTooLarge(
            f"This image is {megapixels} megapixels, more than visual analysis "
            f"decodes ({MAX_DECODED_PIXELS // 1_000_000})."
        )
    picture = _flattened(ImageOps.exif_transpose(source))
    picture.thumbnail((max_edge_px, max_edge_px), Image.Resampling.LANCZOS)
    if not was_jpeg:
        data = _encode(picture, "PNG")
        if len(data) <= max_bytes:
            return VisionImage(data=data, media_type="image/png")
    return _jpeg_within(picture, max_bytes)


def _fitted_size(width: int, height: int, max_edge_px: int) -> tuple[int, int]:
    scale = min(1.0, max_edge_px / max(width, height, 1))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _flattened(picture: PILImage.Image) -> PILImage.Image:
    """``picture`` in L or RGB, any transparency laid over white.

    Some local models drop the alpha channel without compositing it, which
    turns black text on a transparent background into black on black.
    """
    from PIL import Image

    if picture.mode in ("RGBA", "LA", "PA") or "transparency" in picture.info:
        rgba = picture.convert("RGBA")
        flat = Image.new("RGB", rgba.size, _WHITE)
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    if picture.mode in ("L", "RGB"):
        return picture
    return picture.convert("L" if picture.mode == "1" else "RGB")


def _jpeg_within(picture: PILImage.Image, max_bytes: int) -> VisionImage:
    """The best JPEG within ``max_bytes``, shrinking the picture if it must."""
    from PIL import Image

    while True:
        for quality in JPEG_QUALITIES:
            data = _encode(picture, "JPEG", quality=quality)
            if len(data) <= max_bytes:
                return VisionImage(data=data, media_type="image/jpeg")
        width, height = picture.size
        if max(width, height) <= MIN_EDGE_PX:
            return VisionImage(data=data, media_type="image/jpeg")
        picture = picture.resize(
            (max(1, int(width * SHRINK_STEP)), max(1, int(height * SHRINK_STEP))),
            Image.Resampling.LANCZOS,
        )


def _encode(picture: PILImage.Image, image_format: str, **options: int) -> bytes:
    buffer = io.BytesIO()
    if image_format == "JPEG":
        picture.save(buffer, format="JPEG", optimize=True, **options)
    else:
        picture.save(buffer, format=image_format, **options)
    return buffer.getvalue()
