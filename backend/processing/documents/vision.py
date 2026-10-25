"""Turning a page's images into Markdown via a vision-capable model."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from backend.utils.vision import VisionImage, VisionUnsupportedError

from .types import PageSource
from .vision_input import fit_images_for_vision, vision_batches

logger = logging.getLogger(__name__)

# The rendered-page instruction. Two things in here are load-bearing.
#
# First, the extracted text layer is supplied alongside the image. The text
# layer is exact where it exists, and the model is far better spent on what the
# text layer cannot express -- tables, charts, diagrams, handwriting -- than on
# re-typing body copy it might paraphrase.
#
# Second, the model is told to describe rather than interpret. A chart summarised
# as "sales improved" is useless for later retrieval; the axis labels and the
# values are what someone will actually ask about.
_PAGE_PROMPT = """\
You are transcribing one page of a document into Markdown for a search index.

Reproduce the page faithfully:
- Transcribe all visible text. Do not summarise, shorten or paraphrase it.
- Render tables as Markdown tables, preserving every row and column.
- For a chart, state its type and title, then give the axis labels and every
  data value you can read. Do not describe the trend instead of the numbers.
- For a diagram or flowchart, state the labels and the relationships between
  them, including the direction of any arrows.
- For a photograph or screenshot, describe what it shows in enough detail that
  someone who cannot see it understands what it conveys.
- Use headings that match the page's own visual hierarchy.

Return only the Markdown for this page. No preamble, no commentary, and no
fenced code block around the whole answer.\
"""

_TEXT_LAYER_PROMPT = """\

The text layer extracted from this page is below. It is accurate where it has
content, so prefer it verbatim for body text and spend your effort on what it
does not capture -- tables, charts, diagrams, and anything rendered as an image.

<extracted_text>
{text}
</extracted_text>\
"""

# Slide pictures and Word images arrive without a page render around them, so
# the model is describing one figure rather than transcribing a whole page.
_FIGURE_PROMPT = """\
Describe the following image or images from {location} of a document, for a
search index.

State any text visible in the image verbatim. If it is a chart, give its type,
title, axis labels and every readable data value. If it is a diagram, give the
labels and the relationships between them. If it is a photograph or screenshot,
describe what it shows in enough detail that someone who cannot see it
understands what it conveys.

Return only the description as Markdown. No preamble and no commentary.\
"""


def build_page_prompt(page: PageSource) -> str:
    """The instruction for a rendered page, with its text layer when there is one."""
    prompt = _PAGE_PROMPT
    text = (page.text or "").strip()
    if text:
        prompt += _TEXT_LAYER_PROMPT.format(text=text)
    return prompt


def build_figure_prompt(page: PageSource) -> str:
    location = (
        f'the slide titled "{page.title}"' if page.title else f"page {page.page_number}"
    )
    return _FIGURE_PROMPT.format(location=location)


@dataclass(frozen=True)
class PageTranscription:
    """What visual analysis made of one page's images.

    ``left_out`` counts pictures Pillow cannot open, which no model was sent.
    ``failed`` counts pictures whose call failed; when every call failed,
    ``text`` is None and the page as a whole failed.
    """

    text: Optional[str] = None
    left_out: int = 0
    failed: int = 0

    @property
    def page_failed(self) -> bool:
        return self.text is None and self.failed > 0


def transcribe_page(
    backend,
    page: PageSource,
    *,
    is_rendered_page: bool,
    timeout: int = 180,
) -> PageTranscription:
    """Markdown for one page's images, from one call or several.

    The pictures go in order, as many to a call as the providers' request
    limits allow (``vision_batches``), and the answers are joined in that
    order. A failed call costs only its own pictures: the page keeps what the
    other calls described, and the page fails only when every call did.

    ``VisionUnsupportedError`` is deliberately allowed to propagate: it means
    the model will never do this, so the orchestrator downgrades the whole
    document once rather than failing every page in turn. Any other exception,
    such as ``VisionImageTooLarge``, fails the page and is handled by the caller.

    The model gets copies fitted to the providers' limits; ``page.images``
    keeps the originals for OCR.
    """
    if not page.images:
        return PageTranscription()

    prompt = build_page_prompt(page) if is_rendered_page else build_figure_prompt(page)
    images, left_out = fit_images_for_vision(page.images)
    answers: List[str] = []
    failed = 0
    for batch in vision_batches(images):
        try:
            text = backend.generate_text_from_images(prompt, batch, timeout=timeout)
        except VisionUnsupportedError:
            raise
        except Exception as e:  # noqa: BLE001 -- boundary: one failed call loses only its own pictures, and the page's warning counts them
            failed += len(batch)
            logger.warning(
                "Vision call failed for %s of %s pictures on page %s: %s",
                len(batch),
                len(images),
                page.page_number,
                e,
            )
            continue
        text = _strip_code_fence((text or "").strip())
        if text:
            answers.append(text)
    return PageTranscription(
        text="\n\n".join(answers) or None, left_out=left_out, failed=failed
    )


def _strip_code_fence(text: str) -> str:
    """Unwrap a whole-answer code fence.

    Models wrap Markdown in ```markdown despite being told not to, often enough
    that leaving it would put fence markers into the search index and into the
    notes prompt. Only a fence around the *entire* answer is removed -- a code
    block that is genuinely part of the page keeps its fences.
    """
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2 or not lines[-1].strip().startswith("```"):
        return text
    return "\n".join(lines[1:-1]).strip()


def merge_page_content(
    page: PageSource,
    vision_text: Optional[str],
    *,
    is_rendered_page: bool,
) -> str:
    """Combine structural text with whatever the vision pass produced.

    The two cases genuinely differ. For a rendered page the vision output
    *replaces* the text layer: the model was handed that text and asked to
    produce a more complete version of it, so keeping both would duplicate
    every paragraph. For a figure it only *supplements*: the slide's own text
    frames, tables, chart data and speaker notes are exact and were never shown
    to the model, so a picture description is additive.
    """
    structural = (page.text or "").strip()
    described = (vision_text or "").strip()
    if not described:
        return structural
    if is_rendered_page or not structural:
        return described
    return f"{structural}\n\n{described}"


__all__ = [
    "PageTranscription",
    "VisionUnsupportedError",
    "VisionImage",
    "build_figure_prompt",
    "build_page_prompt",
    "merge_page_content",
    "transcribe_page",
]
