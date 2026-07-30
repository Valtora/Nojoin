"""Local OCR, the middle tier between a vision model and plain structural text.

Exists so a scanned page is readable with no AI provider at all. It runs on the
worker, costs nothing, sends nothing anywhere, and needs no configuration.

What it is not: a replacement for visual analysis. OCR transcribes glyphs. It
cannot say what a chart shows, describe a diagram's flow, or read a photograph,
so it sits *below* the vision tier and is only consulted when that tier is
unavailable or declined the page.

Tesseract is driven through the system binary rather than an ONNX OCR package on
purpose: every Python OCR library worth using depends on ``onnxruntime``, and
the worker image deliberately replaces that with ``onnxruntime-gpu`` (see
docker/Dockerfile.worker). Reintroducing the CPU build alongside it is the exact
conflict that pin exists to prevent.
"""

from __future__ import annotations

import functools
import io
import logging
import shutil
from typing import List, Optional, Sequence

from backend.utils.vision import VisionImage

logger = logging.getLogger(__name__)

# Page segmentation mode 3: fully automatic, no orientation detection. The right
# default for a document page, which is what every caller passes.
_TESSERACT_CONFIG = "--psm 3"

# Below this many characters, a page's OCR result is treated as noise rather
# than content. Tesseract emits stray punctuation for a blank or purely
# decorative page, and indexing that is worse than indexing nothing.
MIN_USEFUL_OCR_CHARS = 24


@functools.lru_cache(maxsize=1)
def ocr_is_available() -> bool:
    """Whether the tesseract binary and its Python wrapper are both present.

    Cached: this is consulted once per page and the answer cannot change within
    a worker's lifetime. Absence is not an error -- OCR is an optional tier, and
    a deployment without it simply falls through to structural text.
    """
    if shutil.which("tesseract") is None:
        logger.info("OCR unavailable: the tesseract binary is not installed.")
        return False
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        logger.info("OCR unavailable: pytesseract is not installed.")
        return False
    return True


def _ocr_one(image: VisionImage) -> str:
    import pytesseract
    from PIL import Image

    with Image.open(io.BytesIO(image.data)) as handle:
        # Tesseract wants no alpha channel, and a page render may carry one.
        if handle.mode not in ("L", "RGB"):
            handle = handle.convert("RGB")
        return pytesseract.image_to_string(handle, config=_TESSERACT_CONFIG)


def ocr_images(images: Sequence[VisionImage]) -> Optional[str]:
    """Transcribe one page's images, or None when nothing useful came back.

    Returns None rather than an empty string so the caller can distinguish "OCR
    found nothing" from "OCR was never run", which decide different things: the
    first leaves the page on its structural text, the second may still be worth
    warning about.
    """
    if not images or not ocr_is_available():
        return None

    fragments: List[str] = []
    for index, image in enumerate(images):
        try:
            text = _ocr_one(image)
        except Exception as e:  # noqa: BLE001 - one unreadable image is survivable
            logger.warning("OCR failed on image %s: %s", index, e)
            continue
        cleaned = (text or "").strip()
        if cleaned:
            fragments.append(cleaned)

    combined = "\n\n".join(fragments).strip()
    if len(combined) < MIN_USEFUL_OCR_CHARS:
        return None
    return combined
