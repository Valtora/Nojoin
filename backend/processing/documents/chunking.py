"""Cutting a parsed page into embeddable chunks.

The whole point of moving to an 8192-token embedding model is that a page is
normally *one* chunk: a slide, a PDF page or a report section is a coherent
retrieval unit, and slicing it into 500-character windows was what made the old
index return sentence fragments with no context.

Splitting therefore only happens when a page genuinely cannot fit -- a large
worksheet, mostly -- and even then it splits on structure rather than on a
character count, so a Markdown table is never cut through the middle of a row.
"""

from __future__ import annotations

from typing import List

from backend.processing.text_embedding_version import TEXT_EMBEDDING_MAX_CHUNK_CHARS


def _split_oversized_block(block: str, budget: int) -> List[str]:
    """Split one block that is itself larger than the budget.

    Lines first, because an oversized block is almost always a long table and a
    row boundary is the only cut that keeps it readable. A single line longer
    than the budget is cut bluntly -- at that point there is no structure left
    to respect.
    """
    lines = block.splitlines()
    chunks: List[str] = []
    current: List[str] = []
    length = 0

    for line in lines:
        if len(line) > budget:
            if current:
                chunks.append("\n".join(current))
                current, length = [], 0
            for start in range(0, len(line), budget):
                chunks.append(line[start : start + budget])
            continue
        if length + len(line) + 1 > budget and current:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1

    if current:
        chunks.append("\n".join(current))
    return [chunk for chunk in chunks if chunk.strip()]


def chunk_page_content(
    content: str,
    *,
    budget: int = TEXT_EMBEDDING_MAX_CHUNK_CHARS,
) -> List[str]:
    """One chunk per page where it fits, otherwise split on paragraph breaks.

    Returns an empty list for an empty page, which the caller treats as "index
    nothing" rather than as an error -- a genuinely blank slide is normal.
    """
    text = (content or "").strip()
    if not text:
        return []
    if len(text) <= budget:
        return [text]

    blocks = [block for block in text.split("\n\n") if block.strip()]
    chunks: List[str] = []
    current: List[str] = []
    length = 0

    for block in blocks:
        if len(block) > budget:
            if current:
                chunks.append("\n\n".join(current))
                current, length = [], 0
            chunks.extend(_split_oversized_block(block, budget))
            continue
        if length + len(block) + 2 > budget and current:
            chunks.append("\n\n".join(current))
            current, length = [], 0
        current.append(block)
        length += len(block) + 2

    if current:
        chunks.append("\n\n".join(current))
    return [chunk for chunk in chunks if chunk.strip()]
