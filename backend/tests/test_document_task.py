"""Orchestrator behaviour: vision fan-out, downgrade, resume, and staleness.

These exercise the decision logic around the parse rather than the parsers
themselves (covered in test_document_parsing.py), using stub backends so no
provider is contacted.
"""

from __future__ import annotations

import pytest

from backend.processing.documents.types import PageSource
from backend.processing.llm_backends.base import (
    LLMBackend,
    is_vision_unsupported_error,
)
from backend.utils.vision import VisionImage, VisionUnsupportedError
from backend.worker.tasks.documents import _transcribe_batch


def _page(number: int, *, with_image: bool = True) -> PageSource:
    return PageSource(
        page_number=number,
        text=f"structural text {number}",
        images=(
            [VisionImage(data=b"png", media_type="image/png")] if with_image else []
        ),
    )


class _Backend:
    """Records calls and replays a scripted outcome per page."""

    def __init__(self, outcomes=None):
        self.outcomes = outcomes or {}
        self.calls = []

    def generate_text_from_images(self, prompt, images, timeout=120, max_tokens=8192):
        self.calls.append(prompt)
        # The page number is recoverable from the structural text in the prompt.
        for number, outcome in self.outcomes.items():
            if f"structural text {number}" in prompt:
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        return "transcribed"


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


def test_every_page_with_an_image_is_transcribed():
    backend = _Backend()
    results = _transcribe_batch(
        backend, [_page(1), _page(2), _page(3)], is_rendered=True
    )
    assert results == {1: "transcribed", 2: "transcribed", 3: "transcribed"}
    assert len(backend.calls) == 3


def test_pages_without_images_are_not_sent():
    backend = _Backend()
    results = _transcribe_batch(backend, [_page(1, with_image=False)], is_rendered=True)
    assert results == {}
    assert backend.calls == []


def test_an_empty_batch_makes_no_calls():
    backend = _Backend()
    assert _transcribe_batch(backend, [], is_rendered=True) == {}
    assert backend.calls == []


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_one_page_failing_does_not_lose_the_others():
    """A transient provider error on page 2 must not abandon a long document;
    that page falls back to its structural text."""
    backend = _Backend({2: RuntimeError("provider hiccup")})
    results = _transcribe_batch(
        backend, [_page(1), _page(2), _page(3)], is_rendered=True
    )
    assert set(results) == {1, 3}


def test_a_page_returning_nothing_is_omitted_rather_than_stored_blank():
    backend = _Backend({2: "   "})
    results = _transcribe_batch(backend, [_page(1), _page(2)], is_rendered=True)
    assert set(results) == {1}


def test_vision_unsupported_propagates_so_the_document_downgrades_once():
    """Unlike a per-page failure, this condemns every remaining page. It must
    reach the caller so the whole document is downgraded and warned about,
    rather than retried uselessly on every page in turn."""
    backend = _Backend({1: VisionUnsupportedError("no vision on this model")})
    with pytest.raises(VisionUnsupportedError):
        _transcribe_batch(backend, [_page(1), _page(2)], is_rendered=True)


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "400 does not support image input",
        "This model does not support vision",
        "Invalid content type: image_url is not supported",
        "multimodal input rejected",
    ],
)
def test_provider_refusals_are_recognised_as_missing_vision(message):
    assert is_vision_unsupported_error(RuntimeError(message))


@pytest.mark.parametrize(
    "message",
    [
        "429 rate limit exceeded",
        "connection reset by peer",
        "500 internal server error",
    ],
)
def test_transient_errors_are_not_mistaken_for_missing_vision(message):
    """Getting this wrong would permanently downgrade a document over one
    flaky call."""
    assert not is_vision_unsupported_error(RuntimeError(message))


def test_the_default_backend_reports_no_vision_and_no_capability_answer():
    backend = LLMBackend()
    assert backend.supports_vision() is None
    with pytest.raises(VisionUnsupportedError):
        backend.generate_text_from_images("prompt", [])
