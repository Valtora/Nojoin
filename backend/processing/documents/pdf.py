"""PDF parsing: text and page images via pypdfium2, ruled tables via pdfplumber.

pypdfium2 wraps PDFium, the PDF engine in Chrome. It reads the text layer in the
order the file draws it, which for any ordinary generator is column by column,
and renders pages for the vision tier. pdfplumber, on pdfminer.six, finds tables
from their ruling lines. Tables are best-effort: a file pdfplumber cannot read
still yields PDFium's text and images.
"""

from __future__ import annotations

import io
import logging
from typing import Any, Iterator, List, Optional

from backend.utils.vision import VisionImage

from .markdown_tables import rows_to_markdown_table
from .types import DocumentSource, PageSource
from .vision_input import MAX_EDGE_PX

logger = logging.getLogger(__name__)

# Long-edge target for a rendered page, in pixels. Vision models downscale
# anything larger, so rendering beyond this spends encode time and tokens to
# produce an image the model will shrink anyway. 1600 keeps 8pt body text
# legible on A4 without crossing that line.
RENDER_MAX_EDGE_PX = 1600

# Floor on the zoom factor. It only binds on pages whose long edge exceeds about
# 15 inches (A3 and larger), where 1600 px would leave small print illegible, so
# those render somewhat larger, up to the ceiling below.
RENDER_MIN_ZOOM = 1.5

# Hard ceiling on a rendered page's long edge, whatever the floor asks for. A
# page can be 200 inches square, and a floor-only zoom rendered that as a
# 21,600 px bitmap (1.4 GB) that ended in an out-of-memory kill. The ceiling is
# the largest long edge any supported provider uses (vision_input.MAX_EDGE_PX).
# At most 2576 x 2576, the bitmap stays under 27 MB.
RENDER_CEILING_PX = MAX_EDGE_PX

# Pillow's level 3 encodes a rendered page faster than its default 6 and, on
# text pages, into a smaller file.
PNG_COMPRESS_LEVEL = 3


def render_zoom(width: float, height: float) -> float:
    """The scale for a page of this size in points, within the ceiling."""
    longest = max(width, height) or 1
    zoom = max(RENDER_MIN_ZOOM, RENDER_MAX_EDGE_PX / longest)
    return min(zoom, RENDER_CEILING_PX / longest)


def _render_page(page: Any) -> VisionImage:
    """Rasterise one page to PNG at a size worth sending to a vision model."""
    width, height = page.get_size()
    bitmap = page.render(scale=render_zoom(width, height))
    try:
        buffer = io.BytesIO()
        bitmap.to_pil().save(buffer, format="PNG", compress_level=PNG_COMPRESS_LEVEL)
    finally:
        bitmap.close()
    return VisionImage(data=buffer.getvalue(), media_type="image/png")


def _page_text(page: Any) -> str:
    textpage = page.get_textpage()
    try:
        text = textpage.get_text_bounded()
    finally:
        textpage.close()
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _has_paths(page: Any) -> bool:
    """Whether the page draws any vector path, form XObjects included.

    A ruled table is made of paths, so a page without one has no table for
    pdfplumber to find, and skipping it halves table time on prose pages.
    """
    import pypdfium2.raw as pdfium_c

    return any(True for _ in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_PATH]))


class _TableReader:
    """pdfplumber over the same file, or nothing when it cannot read it."""

    def __init__(self, path: str, page_count: int) -> None:
        self._pdf: Any = None
        self._pages: List[Any] = []
        try:
            import pdfplumber

            self._pdf = pdfplumber.open(path)
            self._pages = list(self._pdf.pages)
        except Exception as e:  # noqa: BLE001 -- boundary: tables are best-effort, text and images still come from PDFium
            logger.warning("Table detection unavailable for %s: %s", path, e)
            self.close()
            return
        if len(self._pages) != page_count:
            logger.warning(
                "Table detection skipped for %s: pdfplumber read %d of %d pages.",
                path,
                len(self._pages),
                page_count,
            )
            self.close()

    def tables(self, index: int) -> List[str]:
        """Tables on one page as Markdown, or nothing if none can be read.

        Emitted alongside the text layer rather than instead of it. A table's
        cells also appear in the flowed text, so this duplicates a little
        content, which is the right trade: the flowed version is unusable for
        lookups, and losing a table to an over-clever de-duplication is worse
        than repeating it.
        """
        if not self._pages:
            return []
        page = self._pages[index]
        tables: List[str] = []
        try:
            for table in page.find_tables():
                try:
                    rows = table.extract()
                except Exception as e:  # noqa: BLE001 -- boundary: one unreadable table must not lose the page
                    logger.debug("Table extraction failed: %s", e)
                    continue
                markdown = rows_to_markdown_table(rows)
                if markdown:
                    tables.append(markdown)
        except Exception as e:  # noqa: BLE001 -- boundary: table finding is best-effort
            logger.debug("Table detection failed on page %d: %s", index + 1, e)
        finally:
            # pdfplumber caches every character and line it parsed on the page.
            page.close()
        return tables

    def close(self) -> None:
        self._pages = []
        if self._pdf is not None:
            self._pdf.close()
            self._pdf = None


def open_pdf(path: str, *, want_images: bool) -> DocumentSource:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path)
    page_count = len(document)

    def pages() -> Iterator[PageSource]:
        reader: Optional[_TableReader] = None
        try:
            reader = _TableReader(path, page_count)
            for index in range(page_count):
                page = document[index]
                try:
                    text = _page_text(page)
                    tables = reader.tables(index) if _has_paths(page) else []
                    if tables:
                        text = "\n\n".join([text.strip(), *tables]).strip()
                    images = [_render_page(page)] if want_images else []
                finally:
                    page.close()
                yield PageSource(
                    page_number=index + 1,
                    title=None,
                    text=text.strip(),
                    images=images,
                )
        finally:
            if reader is not None:
                reader.close()
            document.close()

    return DocumentSource(page_count=page_count, pages=pages)
