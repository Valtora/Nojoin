"""Images fitted to the vision providers' limits before a model call (#698),
and split into calls within their request limits (#714).

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
    MAX_BATCH_BYTES,
    MAX_BATCH_IMAGES,
    MAX_EDGE_PX,
    MAX_IMAGE_BYTES,
    VisionImageTooLarge,
    VisionImageUnreadable,
    fit_image_for_vision,
    fit_images_for_vision,
    vision_batches,
)
from backend.tests.picture_fixtures import emf_drawing
from backend.utils.vision import VisionImage

# The Claude API's per-image limit, counted in base64, in its strictest reading.
CLAUDE_BASE64_LIMIT = 5 * 1024 * 1024
# The tightest request limit of the providers: Gemini's 20 MB of inline data,
# read as base64 and as decimal megabytes. The Claude API takes 32 MB.
GEMINI_REQUEST_LIMIT = 20_000_000
# Past this many images in one request the Claude API takes 2000 px a side.
CLAUDE_FULL_SIZE_IMAGES = 20


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


def _cut_short_png() -> bytes:
    return _encode(_noise((300, 300)), "PNG")[:4000]


@pytest.mark.parametrize(
    "make,media_type",
    [
        (emf_drawing, "image/x-emf"),
        (_cut_short_png, "image/png"),
        (lambda: b"not an image at all", "image/png"),
    ],
    ids=["emf", "cut-short-png", "junk"],
)
def test_bytes_pillow_cannot_read_are_refused(make, media_type):
    """Sent as they were, a provider that refuses the type failed the call for
    every picture beside them (#714)."""
    with pytest.raises(VisionImageUnreadable):
        fit_image_for_vision(VisionImage(data=make(), media_type=media_type))


def test_a_page_leaves_out_what_pillow_cannot_read_and_keeps_the_order():
    first = _encode(Image.new("RGB", (64, 64), (200, 0, 0)), "PNG")
    second = _encode(Image.new("RGB", (64, 64), (0, 0, 200)), "PNG")
    images = [
        VisionImage(data=first, media_type="image/png"),
        VisionImage(data=emf_drawing(), media_type="image/x-emf"),
        VisionImage(data=second, media_type="image/png"),
        VisionImage(data=b"junk", media_type="image/x-wmf"),
    ]

    fitted, left_out = fit_images_for_vision(images)

    assert [image.data for image in fitted] == [first, second]
    assert left_out == 2


def test_an_image_past_the_pixel_cap_is_refused(monkeypatch):
    monkeypatch.setattr(vision_input, "MAX_DECODED_PIXELS", 10_000)
    data = _encode(Image.new("RGB", (3000, 10)), "PNG")

    with pytest.raises(VisionImageTooLarge):
        fit_image_for_vision(VisionImage(data=data, media_type="image/png"))


def test_a_page_with_many_pictures_keeps_them_at_full_size():
    """Calls never carry more than 20 images, past which the Claude API takes
    2000 px a side, so no picture is shrunk for having many neighbours."""
    data = _encode(Image.new("RGB", (2400, 60), (10, 10, 10)), "PNG")
    images = [VisionImage(data=data, media_type="image/png")] * 30

    fitted, left_out = fit_images_for_vision(images)

    assert left_out == 0
    assert {_opened(image).size for image in fitted} == {(2400, 60)}
    assert MAX_BATCH_IMAGES <= CLAUDE_FULL_SIZE_IMAGES
    assert max(len(batch) for batch in vision_batches(fitted)) <= MAX_BATCH_IMAGES


def _sized(size_bytes: int, label: int) -> VisionImage:
    return VisionImage(data=bytes([label]) * size_bytes, media_type="image/jpeg")


def _base64_size(batch) -> int:
    return sum(len(image.to_base64()) for image in batch)


def test_ten_large_photos_go_in_calls_under_every_request_limit():
    """Ten photos at the per-image limit are 47 MB of base64: past the Claude
    API's 32 MB request limit in one call, which lost every one of them."""
    photos = [_sized(MAX_IMAGE_BYTES, label) for label in range(10)]

    batches = vision_batches(photos)

    assert _base64_size(photos) > 32 * 1024 * 1024
    assert [image for batch in batches for image in batch] == photos
    assert all(_base64_size(batch) <= GEMINI_REQUEST_LIMIT for batch in batches)
    assert all(sum(len(i.data) for i in batch) <= MAX_BATCH_BYTES for batch in batches)
    # As large as the budget allows: four at the per-image limit to a call.
    assert [len(batch) for batch in batches] == [4, 4, 2]


def test_small_pictures_go_as_many_to_a_call_as_the_image_budget_allows():
    pictures = [_sized(50_000, label) for label in range(MAX_BATCH_IMAGES * 2 + 3)]

    batches = vision_batches(pictures)

    assert [len(batch) for batch in batches] == [MAX_BATCH_IMAGES, MAX_BATCH_IMAGES, 3]
    assert [image for batch in batches for image in batch] == pictures


def test_a_picture_past_the_byte_budget_still_goes_alone():
    """Fitting keeps every picture far below the budget; this only shows that
    the split never drops or stalls on one that is not."""
    huge = _sized(MAX_BATCH_BYTES + 1, 1)
    small = _sized(10, 2)

    assert vision_batches([small, huge, small]) == [[small], [huge], [small]]
    assert vision_batches([]) == []
