"""The local OCR tier: availability, thresholds, and where it sits.

OCR is the middle tier between a vision model and a format's own text layer. The
tests that matter are about *when* it runs, not about tesseract's accuracy:
running it on a page that already has a good text layer makes that page worse,
and failing to run it on a scanned page is the gap it exists to close.

No test requires the tesseract binary. Availability is faked, so the suite
behaves identically on a developer host without it and in the worker image.
"""

from __future__ import annotations

import pytest

from backend.models.document_page import PageParseMode
from backend.processing.documents import ocr as ocr_module
from backend.processing.documents.types import PageSource
from backend.utils.vision import VisionImage
from backend.worker.tasks import documents as documents_task


@pytest.fixture(autouse=True)
def _clear_availability_cache():
    """ocr_is_available is lru_cached, so each test must start from scratch."""
    _clear()
    yield
    _clear()


def _clear():
    # monkeypatch may have swapped the cached function for a plain callable.
    clear = getattr(ocr_module.ocr_is_available, "cache_clear", None)
    if clear is not None:
        clear()


def _image() -> VisionImage:
    return VisionImage(data=b"\x89PNGfake", media_type="image/png")


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def test_ocr_is_unavailable_without_the_binary(monkeypatch):
    """A deployment with no tesseract falls through to structural text rather
    than failing, so absence must be detected, not assumed."""
    monkeypatch.setattr(ocr_module.shutil, "which", lambda _name: None)
    assert ocr_module.ocr_is_available() is False


def test_ocr_is_available_when_binary_and_wrapper_are_present(monkeypatch):
    monkeypatch.setattr(ocr_module.shutil, "which", lambda _name: "/usr/bin/tesseract")
    pytest.importorskip("pytesseract")
    assert ocr_module.ocr_is_available() is True


def test_no_images_means_no_ocr(monkeypatch):
    monkeypatch.setattr(ocr_module.shutil, "which", lambda _n: "/usr/bin/tesseract")
    assert ocr_module.ocr_images([]) is None


def test_unavailable_ocr_returns_none_rather_than_raising(monkeypatch):
    monkeypatch.setattr(ocr_module.shutil, "which", lambda _name: None)
    assert ocr_module.ocr_images([_image()]) is None


# ---------------------------------------------------------------------------
# Result filtering
# ---------------------------------------------------------------------------


def test_a_trivial_result_is_discarded_as_noise(monkeypatch):
    """Tesseract emits stray punctuation for a blank or decorative page, and
    indexing that is worse than indexing nothing."""
    monkeypatch.setattr(ocr_module.shutil, "which", lambda _n: "/usr/bin/tesseract")
    monkeypatch.setattr(ocr_module, "_ocr_one", lambda _image: ". , -")
    assert ocr_module.ocr_images([_image()]) is None


def test_a_substantial_result_is_kept(monkeypatch):
    body = "Quarterly revenue grew twelve per cent across the EMEA region."
    monkeypatch.setattr(ocr_module.shutil, "which", lambda _n: "/usr/bin/tesseract")
    monkeypatch.setattr(ocr_module, "_ocr_one", lambda _image: body)
    assert ocr_module.ocr_images([_image()]) == body


def test_one_unreadable_image_does_not_lose_the_others(monkeypatch):
    calls = {"n": 0}

    def _flaky(_image):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("corrupt image")
        return "Readable content recovered from the second image on the page."

    monkeypatch.setattr(ocr_module.shutil, "which", lambda _n: "/usr/bin/tesseract")
    monkeypatch.setattr(ocr_module, "_ocr_one", _flaky)
    result = ocr_module.ocr_images([_image(), _image()])
    assert result is not None
    assert "Readable content" in result


# ---------------------------------------------------------------------------
# When the tier runs
# ---------------------------------------------------------------------------


def _writer(monkeypatch, *, ocr_result):
    monkeypatch.setattr(documents_task, "ocr_images", lambda _images: ocr_result)

    class _Session:
        def add(self, _obj):
            pass

        def commit(self):
            pass

    class _Doc:
        id = 1

    return documents_task._PageWriter(
        _Session(),
        _Doc(),
        backend=None,
        use_vision=False,
        is_rendered=True,
        already_parsed=0,
    )


def test_ocr_runs_on_a_page_with_images_and_no_text_layer(monkeypatch):
    writer = _writer(monkeypatch, ocr_result="Recovered scanned text.")
    page = PageSource(page_number=1, text="", images=[_image()])

    assert writer._ocr_fallback(page) == "Recovered scanned text."


def test_ocr_is_skipped_when_the_page_already_has_a_text_layer(monkeypatch):
    """OCR transcribes glyphs, so on a page with a real text layer it can only
    be worse. Running it anyway would degrade every normal PDF."""
    writer = _writer(monkeypatch, ocr_result="worse version of the same text")
    page = PageSource(
        page_number=1,
        text="x" * (documents_task.OCR_TEXT_LAYER_THRESHOLD + 1),
        images=[_image()],
    )

    assert writer._ocr_fallback(page) is None


def test_ocr_is_skipped_when_there_is_no_image_to_read(monkeypatch):
    writer = _writer(monkeypatch, ocr_result="should never be used")
    page = PageSource(page_number=1, text="", images=[])

    assert writer._ocr_fallback(page) is None


# ---------------------------------------------------------------------------
# Tier precedence
# ---------------------------------------------------------------------------


def test_a_vision_result_wins_over_ocr(monkeypatch):
    """Vision describes charts and diagrams; OCR cannot. The richer tier must
    take precedence whenever it produced anything."""
    writer = _writer(monkeypatch, ocr_result="ocr text")
    monkeypatch.setattr(
        documents_task,
        "_transcribe_batch",
        lambda *a, **k: {1: "a described chart"},
    )
    writer.use_vision = True
    writer._backend = object()

    writer.flush([PageSource(page_number=1, text="", images=[_image()])])

    assert writer.stored[0].parse_mode == PageParseMode.VISUAL
    assert writer.stored[0].content == "a described chart"
    assert writer.used_ocr is False


def test_ocr_is_used_when_vision_produced_nothing(monkeypatch):
    writer = _writer(monkeypatch, ocr_result="ocr text")
    writer.flush([PageSource(page_number=1, text="", images=[_image()])])

    assert writer.stored[0].parse_mode == PageParseMode.OCR
    assert writer.stored[0].content == "ocr text"
    assert writer.used_ocr is True


def test_structural_text_survives_when_neither_tier_produced_anything(monkeypatch):
    writer = _writer(monkeypatch, ocr_result=None)
    writer.flush(
        [PageSource(page_number=1, text="the format's own text", images=[_image()])]
    )

    assert writer.stored[0].parse_mode == PageParseMode.STRUCTURAL
    assert writer.stored[0].content == "the format's own text"
    assert writer.used_ocr is False


def test_ocr_supplements_rather_than_replaces_a_text_layer(monkeypatch):
    """Even on a rendered-page format, OCR must never displace the text layer
    the way a vision transcription does -- it is strictly worse at text."""
    writer = _writer(monkeypatch, ocr_result="text from the embedded screenshot")
    writer.flush([PageSource(page_number=1, text="a caption", images=[_image()])])

    content = writer.stored[0].content
    assert "a caption" in content
    assert "text from the embedded screenshot" in content
