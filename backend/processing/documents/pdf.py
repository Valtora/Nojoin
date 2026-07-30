"""PDF parsing via PyMuPDF: structural text, tables, and page rendering."""

from __future__ import annotations

import logging
from typing import Iterator, List

from backend.utils.vision import VisionImage

from .markdown_tables import rows_to_markdown_table
from .types import DocumentSource, PageSource

logger = logging.getLogger(__name__)

# Long-edge target for a rendered page, in pixels. Vision models downscale
# anything larger, so rendering beyond this spends encode time and tokens to
# produce an image the model will shrink anyway. 1600 keeps 8pt body text
# legible on A4 without crossing that line.
RENDER_MAX_EDGE_PX = 1600

# Floor on the zoom factor so a very small page (a slide-sized PDF) is still
# rendered at usable resolution rather than at its nominal 72 dpi.
RENDER_MIN_ZOOM = 1.5


def _render_page(page) -> VisionImage:
    """Rasterise one page to PNG at a size worth sending to a vision model."""
    rect = page.rect
    longest = max(rect.width, rect.height) or 1
    zoom = max(RENDER_MIN_ZOOM, RENDER_MAX_EDGE_PX / longest)

    import fitz

    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return VisionImage(data=pixmap.tobytes("png"), media_type="image/png")


def _extract_tables(page) -> List[str]:
    """Tables as Markdown, or nothing if this PyMuPDF build cannot find them.

    Emitted alongside the text layer rather than instead of it. A table's cells
    also appear in the flowed text, so this duplicates a little content -- which
    is the right trade: the flowed version is unusable for lookups, and losing
    a table to an over-clever de-duplication is worse than repeating it.
    """
    try:
        finder = page.find_tables()
    except Exception as e:  # noqa: BLE001 - table finding is best-effort
        logger.debug("Table detection unavailable on this page: %s", e)
        return []

    tables: List[str] = []
    for table in getattr(finder, "tables", []):
        try:
            rows = table.extract()
        except Exception as e:  # noqa: BLE001
            logger.debug("Table extraction failed: %s", e)
            continue
        markdown = rows_to_markdown_table(rows)
        if markdown:
            tables.append(markdown)
    return tables


def open_pdf(path: str, *, want_images: bool) -> DocumentSource:
    import fitz

    document = fitz.open(path)
    page_count = document.page_count

    def pages() -> Iterator[PageSource]:
        try:
            for index in range(page_count):
                page = document.load_page(index)
                # sort=True reorders blocks into reading order, which is what
                # separates a usable two-column extraction from interleaved
                # nonsense.
                text = page.get_text("text", sort=True) or ""
                tables = _extract_tables(page)
                if tables:
                    text = "\n\n".join([text.strip(), *tables]).strip()
                images = [_render_page(page)] if want_images else []
                yield PageSource(
                    page_number=index + 1,
                    title=None,
                    text=text.strip(),
                    images=images,
                )
        finally:
            document.close()

    return DocumentSource(page_count=page_count, pages=pages)
