"""Plain-text exports.

Plain text cannot draw, so the letterhead is a ruled header, charts become the
figures they plot, and tables are padded into columns. Notes are printed as the
Markdown they are stored as, with their tables realigned, so they still paste
into anything that reads Markdown. Lines are not wrapped: every editor wraps
for itself, and a hard wrap breaks copying a transcript line.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from . import theme
from .formatting import format_transcript_time
from .markdown_blocks import realign_markdown_tables
from .model import (
    Block,
    Callout,
    ChartBlock,
    CodeBlock,
    ExportDocument,
    Heading,
    ImageBlock,
    Inline,
    ListBlock,
    Paragraph,
    Rule,
    Section,
    StatRow,
    Table,
    TranscriptLine,
)

WIDTH = 80
HEAVY_RULE = "=" * WIDTH
LIGHT_RULE = "-" * WIDTH


def spans_text(spans: Inline, *, line_break: str = "\n") -> str:
    parts = []
    for span in spans:
        if span.text == "\n":
            parts.append(line_break)
        elif span.link and span.link != span.text:
            parts.append(f"{span.text} ({span.link})")
        else:
            parts.append(span.text)
    return "".join(parts)


def _indent(text: str, prefix: str) -> str:
    return "\n".join(
        prefix + line if line else prefix.rstrip() for line in text.split("\n")
    )


def _heading(block: Heading) -> str:
    return "#" * (block.level + 1) + " " + spans_text(block.text, line_break=" ")


def _paragraph(block: Paragraph) -> str:
    text = spans_text(block.text)
    return _indent(text, "> ") if block.tone == "quote" else text


def _list(block: ListBlock) -> str:
    lines = []
    counters: dict[int, int] = {}
    for item in block.items:
        counters = {
            depth: count for depth, count in counters.items() if depth <= item.depth
        }
        counters[item.depth] = counters.get(item.depth, 0) + 1
        marker = f"{counters[item.depth]}. " if item.ordered else "- "
        indent = "  " * item.depth
        body = spans_text(item.text).split("\n")
        lines.append(indent + marker + body[0])
        lines.extend(indent + " " * len(marker) + line for line in body[1:])
    return "\n".join(lines)


def table_lines(block: Table) -> list[str]:
    """A pipe table padded into columns, valid Markdown either way."""
    width = block.column_count
    header = [spans_text(cell, line_break=" ") for cell in block.header] + [""] * (
        width - len(block.header)
    )
    rows = [
        [spans_text(cell, line_break=" ") for cell in row] + [""] * (width - len(row))
        for row in block.rows
    ]
    widths = [
        max(3, *(len(row[column]) for row in [header, *rows]))
        for column in range(width)
    ]

    def line(cells: Sequence[str]) -> str:
        padded = [
            cell.rjust(widths[column])
            if column in block.numeric
            else cell.ljust(widths[column])
            for column, cell in enumerate(cells)
        ]
        return ("| " + " | ".join(padded) + " |").rstrip()

    separator = (
        "| "
        + " | ".join(
            "-" * (widths[column] - 1) + ":"
            if column in block.numeric
            else "-" * widths[column]
            for column in range(width)
        )
        + " |"
    )
    return [line(header), separator, *(line(row) for row in rows)]


def _table(block: Table) -> str:
    return "\n".join(table_lines(block))


def _stats(block: StatRow) -> str:
    return "   ".join(f"{stat.label}: {stat.value}" for stat in block.stats)


def _callout(block: Callout) -> str:
    body = render_blocks(block.body)
    if block.title:
        body = f"{block.title}\n{body}"
    return _indent(body, "> ")


def _chart(block: ChartBlock) -> str:
    return render_blocks(block.fallback)


def _image(block: ImageBlock) -> str:
    return render_blocks(block.fallback)


def _code(block: CodeBlock) -> str:
    return _indent(block.text, "    ")


def _rule(block: Rule) -> str:
    return "* * *"


def _transcript(block: TranscriptLine) -> str:
    return (
        f"{format_transcript_time(block.start_seconds)} {block.speaker}: {block.text}"
    )


_RENDERERS: dict[type, Callable[..., str]] = {
    Heading: _heading,
    Paragraph: _paragraph,
    ListBlock: _list,
    Table: _table,
    StatRow: _stats,
    Callout: _callout,
    ChartBlock: _chart,
    ImageBlock: _image,
    CodeBlock: _code,
    Rule: _rule,
}


def render_blocks(blocks: Sequence[Block]) -> str:
    """Blocks separated by blank lines; transcript lines run on line by line."""
    chunks: list[str] = []
    previous_was_line = False
    for block in blocks:
        if isinstance(block, TranscriptLine):
            line = _transcript(block)
            if previous_was_line:
                chunks[-1] += "\n" + line
            else:
                chunks.append(line)
            previous_was_line = True
            continue
        previous_was_line = False
        chunks.append(_RENDERERS[type(block)](block))
    return "\n\n".join(chunk for chunk in chunks if chunk)


def _section(section: Section) -> str:
    title = section.title.upper()
    if section.markdown_source is not None:
        body = realign_markdown_tables(section.markdown_source.strip())
    else:
        body = render_blocks(section.blocks)
    return f"{title}\n{LIGHT_RULE}\n\n{body}"


def _letterhead(document: ExportDocument) -> list[str]:
    lines = []
    if document.branded:
        label = "Nojoin · Meeting record"
        lines += [label + document.dated.rjust(WIDTH - len(label)), HEAVY_RULE]
    lines.append(document.title)
    lines += [f"{item.label}: {item.value}" for item in document.meta]
    lines.append(HEAVY_RULE)
    return lines


def render_txt(document: ExportDocument) -> str:
    parts = ["\n".join(_letterhead(document))]
    parts += [_section(section) for section in document.sections]
    if document.branded:
        parts.append(f"{LIGHT_RULE}\n{theme.GENERATED_BY} · {theme.SITE_URL}")
    return "\n\n\n".join(parts) + "\n"
