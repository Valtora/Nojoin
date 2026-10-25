"""Pictures for the document parsing tests, generated rather than stored.

A picture carries its label in a JPEG comment, which Pillow reads back, so a
stub model can say which pictures each call carried and in what order.
"""

from __future__ import annotations

import io
import os
import struct

from PIL import Image


def emf_drawing() -> bytes:
    """A minimal EMF file: Pillow identifies it, but cannot draw it off Windows."""
    header = struct.pack(
        "<II4i4iIIIIHHIIIiiii",
        1,  # EMR_HEADER
        88,
        *(0, 0, 99, 99),  # bounds, in pixels
        *(0, 0, 2600, 2600),  # frame, in hundredths of a millimetre
        0x464D4520,  # " EMF"
        0x10000,
        108,  # the file's size in bytes
        2,  # records
        1,  # handles
        0,
        0,
        0,
        0,
        *(1920, 1080),  # the reference device, in pixels
        *(508, 286),  # and in millimetres
    )
    return header + struct.pack("<IIIII", 14, 20, 0, 16, 20)  # EMR_EOF


def jpeg(size: tuple[int, int] = (48, 32), *, noise: bool = False) -> bytes:
    """A JPEG; random noise compresses worst, so it stands in for a photo."""
    width, height = size
    picture = (
        Image.frombytes("RGB", size, os.urandom(width * height * 3))
        if noise
        else Image.new("RGB", size, (90, 140, 200))
    )
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def labelled(data: bytes, label: str) -> bytes:
    """``data``, a JPEG, with ``label`` as its comment: distinct bytes, same picture.

    The comment goes after the JFIF header, which python-docx expects first.
    """
    comment = label.encode("ascii")
    segment = b"\xff\xfe" + struct.pack(">H", len(comment) + 2) + comment
    at = 2
    if data[2:4] == b"\xff\xe0":  # APP0, the JFIF header
        at = 4 + struct.unpack(">H", data[4:6])[0]
    return data[:at] + segment + data[at:]


def label_of(data: bytes) -> str:
    """The label ``labelled`` gave a picture, decoding the whole of it."""
    with Image.open(io.BytesIO(data)) as picture:
        picture.load()
        return picture.info.get("comment", b"").decode("ascii")
