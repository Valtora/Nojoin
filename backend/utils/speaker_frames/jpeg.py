"""Just enough of a JPEG's header to check what the browser sent.

The endpoint keeps a frame only if it is a whole baseline or progressive JPEG,
and reads its size from the frame header rather than trusting the request. No
image library decodes it: nothing on the server ever needs the pixels, and a
decoder is a larger surface than these few lines.
"""

from __future__ import annotations

# Start-of-frame markers: every SOFn except DHT (C4), JPG (C8) and DAC (CC).
_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
# Markers that stand alone, with no length field after them.
_STANDALONE = frozenset({0x01, 0xD8, *range(0xD0, 0xD8)})
_START_OF_SCAN = 0xDA
_END_OF_IMAGE = 0xD9


class InvalidFrame(ValueError):
    """The upload is not a whole JPEG with a frame header."""


def _check_envelope(data: bytes) -> None:
    if len(data) < 6 or data[:3] != b"\xff\xd8\xff":
        raise InvalidFrame("The frame is not a JPEG.")
    if data[-2:] != b"\xff\xd9":
        raise InvalidFrame("The frame is not a whole JPEG.")


def _read_frame_header(data: bytes, index: int) -> tuple[int, int]:
    if index + 9 > len(data):
        raise InvalidFrame("The frame has no JPEG frame header.")
    height = int.from_bytes(data[index + 5 : index + 7], "big")
    width = int.from_bytes(data[index + 7 : index + 9], "big")
    if width == 0 or height == 0:
        raise InvalidFrame("The frame has no size.")
    return width, height


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Width and height from the first start-of-frame header.

    Raises:
        InvalidFrame: the bytes do not start with a JPEG's start-of-image and
            end with its end-of-image, or no frame header comes before the scan.
    """
    _check_envelope(data)
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            raise InvalidFrame("The frame's JPEG markers are malformed.")
        marker = data[index + 1]
        if marker == 0xFF or marker in _STANDALONE:
            # A fill byte, or a marker with no length after it.
            index += 1 if marker == 0xFF else 2
            continue
        if marker in {_START_OF_SCAN, _END_OF_IMAGE}:
            break
        if marker in _SOF_MARKERS:
            return _read_frame_header(data, index)
        length = int.from_bytes(data[index + 2 : index + 4], "big")
        if length < 2:
            raise InvalidFrame("The frame's JPEG markers are malformed.")
        index += 2 + length
    raise InvalidFrame("The frame has no JPEG frame header.")
