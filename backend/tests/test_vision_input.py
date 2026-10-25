"""Images fitted to the vision providers' limits before a model call (#698).

Every image is generated here, at the size each test needs, rather than kept
as a binary in the repository. Random noise stands in for a photo: it is the
worst case for both PNG and JPEG, so a budget that holds for noise holds for
any picture.
"""

from __future__ import annotations

import io
import os

import pytest
from PIL import Image

from backend.processing.documents import vision_input
from backend.processing.documents.vision_input import (
    MANY_IMAGES,
    MANY_IMAGES_MAX_EDGE_PX,
    MAX_EDGE_PX,
    MAX_IMAGE_BYTES,
    VisionImageTooLarge,
    fit_image_for_vision,
    fit_images_for_vision,
)
from backend.utils.vision import VisionImage

# The Claude API's per-image limit, counted in base64, in its strictest reading.
CLAUDE_BASE64_LIMIT = 5 * 1024 * 1024


def _encode(image: Image.Image, image_format: str, **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, **options)
    return buffer.getvalue()


def _noise(size: tuple[int, int]) -> Image.Image:
    width, height = size
    return Image.frombytes("RGB", size, os.urandom(width * height * 3))


def _opened(image: VisionImage) -> Image.Image:
    picture = Image.open(io.BytesIO(image.data))
    picture.load()
    return picture


def test_a_large_photo_is_scaled_and_reencoded_within_every_limit():
    # An 11 MB PNG of 2700 x 1400 px: 15 MB of base64, past the Claude API's
    # limit in either reading of it.
    original = _encode(_noise((2700, 1400)), "PNG", compress_level=1)
    assert len(original) > 10 * 1024 * 1024 * 3 // 4

    fitted = fit_image_for_vision(VisionImage(data=original, media_type="image/png"))

    picture = _opened(fitted)
    assert max(picture.size) == MAX_EDGE_PX
    assert picture.size == (MAX_EDGE_PX, round(1400 * MAX_EDGE_PX / 2700))
    assert len(fitted.data) <= MAX_IMAGE_BYTES
    assert len(fitted.to_base64()) <= CLAUDE_BASE64_LIMIT
    # Noise never fits as PNG, so it goes as JPEG and is labelled as one.
    assert (fitted.media_type, picture.format) == ("image/jpeg", "JPEG")
    assert fitted.path is None


def test_a_large_screenshot_that_compresses_stays_png():
    """A slide or a screen of text keeps PNG's sharp edges when it fits."""
    screen = Image.new("RGB", (5120, 2880), (250, 250, 250))
    for row in range(0, 2880, 40):
        screen.paste((20, 20, 20), (200, row, 4900, row + 8))

    fitted = fit_image_for_vision(
        VisionImage(data=_encode(screen, "PNG"), media_type="image/png")
    )

    picture = _opened(fitted)
    assert (fitted.media_type, picture.format) == ("image/png", "PNG")
    assert picture.size == (MAX_EDGE_PX, 1449)


def test_a_photo_past_the_byte_limit_shrinks_until_it_fits():
    """The JPEG qualities alone do not always reach the limit."""
    photo = _encode(_noise((1200, 900)), "JPEG", quality=95)
    limit = len(photo) // 6

    fitted = fit_image_for_vision(
        VisionImage(data=photo, media_type="image/jpeg"), max_bytes=limit
    )

    assert len(fitted.data) <= limit
    assert max(_opened(fitted).size) < 1200


@pytest.mark.parametrize(
    "image_format,media_type", [("PNG", "image/png"), ("JPEG", "image/jpeg")]
)
def test_a_small_png_or_jpeg_is_sent_unchanged(image_format, media_type):
    data = _encode(Image.new("RGB", (800, 600), (40, 120, 200)), image_format)
    image = VisionImage(data=data, media_type=media_type, path="/data/shot")

    assert fit_image_for_vision(image) is image


def test_exif_rotation_is_applied():
    """A phone photo stored on its side reaches the model upright."""
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    data = _encode(Image.new("RGB", (300, 200), (90, 90, 90)), "JPEG", exif=exif)

    fitted = fit_image_for_vision(VisionImage(data=data, media_type="image/jpeg"))

    picture = _opened(fitted)
    assert picture.size == (200, 300)
    assert picture.getexif().get(0x0112, 1) == 1


def test_transparency_is_laid_over_white():
    """Some local models drop alpha without compositing it, which would turn
    black text on a transparent background into black on black."""
    drawing = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    drawing.paste((0, 0, 0, 255), (150, 100, 250, 200))

    fitted = fit_image_for_vision(
        VisionImage(data=_encode(drawing, "PNG"), media_type="image/png")
    )

    picture = _opened(fitted)
    assert picture.mode == "RGB"
    assert picture.getpixel((5, 5)) == (255, 255, 255)
    assert picture.getpixel((200, 150)) == (0, 0, 0)


def test_a_cmyk_jpeg_is_sent_as_rgb():
    data = _encode(Image.new("CMYK", (400, 300), (0, 80, 160, 0)), "JPEG")

    fitted = fit_image_for_vision(VisionImage(data=data, media_type="image/jpeg"))

    assert _opened(fitted).mode == "RGB"


def test_a_mislabelled_file_is_sent_as_its_bytes_say():
    """A PNG saved under a .jpg name: the providers check the type against the
    bytes, so the label follows the bytes."""
    data = _encode(Image.new("RGB", (64, 64)), "PNG")

    fitted = fit_image_for_vision(VisionImage(data=data, media_type="image/jpeg"))

    assert (fitted.media_type, fitted.data) == ("image/png", data)


def test_a_gif_is_sent_as_png():
    data = _encode(Image.new("P", (64, 64)), "GIF")

    fitted = fit_image_for_vision(VisionImage(data=data, media_type="image/gif"))

    assert (fitted.media_type, _opened(fitted).format) == ("image/png", "PNG")


def test_bytes_pillow_cannot_read_go_as_they_are():
    """A Word file's EMF drawing, say: the provider decides, as before."""
    image = VisionImage(data=b"\x01\x00\x00\x00 not an image", media_type="image/emf")

    assert fit_image_for_vision(image) is image


def test_an_image_past_the_pixel_cap_is_refused(monkeypatch):
    monkeypatch.setattr(vision_input, "MAX_DECODED_PIXELS", 10_000)
    data = _encode(Image.new("RGB", (3000, 10)), "PNG")

    with pytest.raises(VisionImageTooLarge):
        fit_image_for_vision(VisionImage(data=data, media_type="image/png"))


def test_many_images_in_one_call_fit_the_smaller_edge():
    """The Claude API takes at most 2000 px a side past 20 images a request."""
    data = _encode(Image.new("RGB", (2400, 60), (10, 10, 10)), "PNG")
    images = [VisionImage(data=data, media_type="image/png")] * (MANY_IMAGES + 1)

    fitted = fit_images_for_vision(images)

    assert {_opened(image).size for image in fitted} == {(MANY_IMAGES_MAX_EDGE_PX, 50)}
    few = fit_images_for_vision(images[:MANY_IMAGES])
    assert {_opened(image).size for image in few} == {(2400, 60)}
