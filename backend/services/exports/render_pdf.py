"""PDF exports, laid out with ReportLab on a Nojoin letterhead.

The first page opens with a letterhead band (the logo and name, the export
date, and a brand-orange rule), later pages carry a slim running header, and
every page shares a footer with the page count. With branding turned off the
logo, the name and the footer line go, and the orange becomes a neutral slate.

ReportLab replaced markdown-pdf, whose HTML layout repainted a table header
row's background at the same position on every later page (issue #419). Tables
here are native ReportLab tables: a header row repeats only on the pages its
own table continues onto.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import cached_property
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    HRFlowable,
    Indenter,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Spacer,
    TableStyle,
)
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph as RLParagraph
from reportlab.platypus import Table as RLTable

from . import theme
from .charts import fit_text, layout
from .formatting import format_transcript_time
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
    inline_text,
)

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN_X = 18 * mm
FRAME_WIDTH = PAGE_WIDTH - 2 * MARGIN_X
HEADER_TOP = PAGE_HEIGHT - 12 * mm
FIRST_PAGE_TOP = 32 * mm
LATER_PAGE_TOP = 22 * mm
BOTTOM_MARGIN = 20 * mm
FOOTER_BASELINE = 11 * mm

_SAFE_LINK_PREFIXES = ("http://", "https://", "mailto:")


def _hex(value: str) -> Any:
    return HexColor(value)


# -- inline markup ---------------------------------------------------------------


def markup(spans: Inline, link_color: str = theme.BRAND) -> str:
    """Spans as ReportLab paragraph markup."""
    parts = []
    for span in spans:
        if span.text == "\n":
            parts.append("<br/>")
            continue
        text = escape(span.text)
        if span.code:
            text = f'<font face="{theme.FONT_MONO}" size="8.5">{text}</font>'
        if span.bold:
            text = f"<b>{text}</b>"
        if span.italic:
            text = f"<i>{text}</i>"
        if span.strike:
            text = f"<strike>{text}</strike>"
        if span.color:
            text = f'<font color="{span.color}">{text}</font>'
        if span.link and span.link.lower().startswith(_SAFE_LINK_PREFIXES):
            href = escape(span.link, {'"': "&quot;"})
            text = f'<a href="{href}" color="{link_color}"><u>{text}</u></a>'
        parts.append(text)
    return "".join(parts)


# -- styles --------------------------------------------------------------------------


class _Styles:
    def __init__(self, accent: str) -> None:
        self.accent = accent

    def _style(self, name: str, **values: Any) -> ParagraphStyle:
        base: dict[str, Any] = {
            "fontName": theme.FONT_REGULAR,
            "fontSize": 9.5,
            "leading": 14,
            "textColor": _hex(theme.FOREGROUND),
        }
        base.update(values)
        return ParagraphStyle(name, **base)

    @cached_property
    def body(self) -> ParagraphStyle:
        return self._style("body", spaceAfter=6)

    @cached_property
    def muted(self) -> ParagraphStyle:
        return self._style(
            "muted", fontSize=9, leading=13, textColor=_hex(theme.HELPER), spaceAfter=6
        )

    @cached_property
    def small(self) -> ParagraphStyle:
        return self._style(
            "small", fontSize=7.8, leading=11, textColor=_hex(theme.FAINT), spaceAfter=4
        )

    @cached_property
    def quote(self) -> ParagraphStyle:
        return self._style(
            "quote",
            fontName=theme.FONT_ITALIC,
            textColor=_hex(theme.MUTED),
            leftIndent=12,
            spaceAfter=6,
        )

    @cached_property
    def title(self) -> ParagraphStyle:
        return self._style(
            "title", fontName=theme.FONT_SEMIBOLD, fontSize=21, leading=26, spaceAfter=4
        )

    @cached_property
    def meta(self) -> ParagraphStyle:
        return self._style(
            "meta", fontSize=9.5, leading=13.5, textColor=_hex(theme.MUTED)
        )

    @cached_property
    def section(self) -> ParagraphStyle:
        return self._style(
            "section",
            fontName=theme.FONT_SEMIBOLD,
            fontSize=15,
            leading=19,
            spaceBefore=4,
            keepWithNext=1,
        )

    def heading(self, level: int) -> ParagraphStyle:
        sizes = {1: (12.5, 16, 12), 2: (10.5, 14, 9)}
        size, leading, space = sizes.get(level, (9.5, 13, 7))
        return self._style(
            f"h{level}",
            fontName=theme.FONT_SEMIBOLD,
            fontSize=size,
            leading=leading,
            spaceBefore=space,
            spaceAfter=4,
            textColor=_hex(theme.FOREGROUND if level < 3 else theme.MUTED),
            keepWithNext=1,
        )

    def cell(self, *, header: bool, numeric: bool) -> ParagraphStyle:
        return self._style(
            f"cell-{header}-{numeric}",
            fontName=theme.FONT_SEMIBOLD if header else theme.FONT_REGULAR,
            fontSize=8 if header else 8.8,
            leading=10.5 if header else 12,
            textColor=_hex(theme.MUTED if header else theme.FOREGROUND),
            alignment=TA_RIGHT if numeric else 0,
        )

    def list_item(self, depth: int) -> ParagraphStyle:
        return self._style(
            f"list-{depth}",
            leftIndent=14 + depth * 14,
            bulletIndent=3 + depth * 14,
            bulletFontName=theme.FONT_REGULAR,
            spaceAfter=3,
        )

    @cached_property
    def transcript(self) -> ParagraphStyle:
        # A hanging indent keeps every wrapped line clear of the timestamp.
        return self._style(
            "transcript",
            fontSize=9,
            leading=13,
            leftIndent=40,
            firstLineIndent=-40,
            spaceAfter=3,
        )

    @cached_property
    def stat_label(self) -> ParagraphStyle:
        return self._style(
            "stat-label", fontSize=7.5, leading=10, textColor=_hex(theme.HELPER)
        )

    @cached_property
    def stat_value(self) -> ParagraphStyle:
        return self._style(
            "stat-value", fontName=theme.FONT_SEMIBOLD, fontSize=15, leading=19
        )

    @cached_property
    def code(self) -> ParagraphStyle:
        return self._style(
            "code",
            fontName=theme.FONT_MONO,
            fontSize=8,
            leading=11,
            textColor=_hex(theme.MUTED),
        )


# -- charts --------------------------------------------------------------------------


class ChartFlowable(Flowable):
    """A chart laid out to the width the frame offers it."""

    def __init__(self, chart: object) -> None:
        super().__init__()
        self._chart = chart
        self._scene = layout(chart, FRAME_WIDTH)

    def wrap(
        self, available_width: float, available_height: float
    ) -> tuple[float, float]:
        if abs(self._scene.width - available_width) > 0.5:
            self._scene = layout(self._chart, available_width)
        return self._scene.width, self._scene.height

    def draw(self) -> None:
        from .paint import paint_canvas

        paint_canvas(self.canv, self._scene)


# -- blocks ----------------------------------------------------------------------------


_CELL_PADDING = 12.0
_CELL_FONT_SIZE = 8.8


def _longest_word(text: str) -> float:
    return max(
        (
            theme.text_width(word, theme.FONT_SEMIBOLD, _CELL_FONT_SIZE)
            for word in text.split()
        ),
        default=0.0,
    )


def column_widths(block: Table, total: float) -> list[float]:
    """Room for each column's longest word, then the rest by its longest line.

    The minimum stops a short column (a figure such as "198 Hz") breaking
    mid-word beside a column of prose. The share of what remains is capped, so
    a long paragraph wraps instead of starving its neighbours.
    """
    rows = [block.header, *block.rows]
    minimums: list[float] = []
    weights: list[int] = []
    for column in range(block.column_count):
        lines = [
            line
            for row in rows
            if column < len(row)
            for line in inline_text(row[column]).split("\n")
        ]
        minimums.append(
            max((_longest_word(line) for line in lines), default=0.0) + _CELL_PADDING
        )
        weights.append(min(max([4, *(len(line) for line in lines)]), 48))
    floor = sum(minimums)
    if floor >= total:
        return [width * total / floor for width in minimums]
    spare = (total - floor) / sum(weights)
    return [minimum + spare * weight for minimum, weight in zip(minimums, weights)]


class _StoryBuilder:
    def __init__(self, styles: _Styles) -> None:
        self.styles = styles
        self._renderers: dict[type, Callable[[Any], list[Flowable]]] = {
            Heading: self._heading,
            Paragraph: self._paragraph,
            ListBlock: self._list,
            Table: self._table,
            Rule: self._rule,
            CodeBlock: self._code,
            StatRow: self._stats,
            Callout: self._callout,
            ChartBlock: self._chart,
            TranscriptLine: self._transcript,
            ImageBlock: self._image,
        }

    def flowables(self, blocks: Sequence[Block]) -> list[Flowable]:
        story: list[Flowable] = []
        for block in blocks:
            story.extend(self._renderers[type(block)](block))
        return story

    def _para(self, spans: Inline, style: ParagraphStyle) -> RLParagraph:
        return RLParagraph(markup(spans, self.styles.accent), style)

    def _heading(self, block: Heading) -> list[Flowable]:
        return [self._para(block.text, self.styles.heading(block.level))]

    def _paragraph(self, block: Paragraph) -> list[Flowable]:
        style = {
            "muted": self.styles.muted,
            "small": self.styles.small,
            "quote": self.styles.quote,
        }.get(block.tone, self.styles.body)
        return [self._para(block.text, style)]

    def _list(self, block: ListBlock) -> list[Flowable]:
        flowables: list[Flowable] = []
        counters: dict[int, int] = {}
        for item in block.items:
            counters = {
                depth: count for depth, count in counters.items() if depth <= item.depth
            }
            counters[item.depth] = counters.get(item.depth, 0) + 1
            bullet = f"{counters[item.depth]}." if item.ordered else "•"
            flowables.append(
                RLParagraph(
                    markup(item.text, self.styles.accent),
                    self.styles.list_item(item.depth),
                    bulletText=bullet,
                )
            )
        flowables.append(Spacer(1, 4))
        return flowables

    def _table(self, block: Table) -> list[Flowable]:
        has_header = any(inline_text(cell) for cell in block.header)
        rows = [block.header] if has_header else []
        rows += list(block.rows)
        data = [
            [
                self._para(
                    row[column] if column < len(row) else (),
                    self.styles.cell(
                        header=has_header and index == 0,
                        numeric=column in block.numeric,
                    ),
                )
                for column in range(block.column_count)
            ]
            for index, row in enumerate(rows)
        ]
        table = RLTable(
            data,
            colWidths=column_widths(block, FRAME_WIDTH),
            repeatRows=1 if has_header else 0,
            hAlign="LEFT",
        )
        table.setStyle(self._table_style(block, has_header, len(data)))
        return [table, Spacer(1, 10)]

    def _table_style(
        self, block: Table, has_header: bool, row_count: int
    ) -> TableStyle:
        commands: list[tuple[Any, ...]] = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, _hex(theme.HAIRLINE)),
        ]
        if has_header:
            commands += [
                ("BACKGROUND", (0, 0), (-1, 0), _hex(theme.TABLE_HEADER_FILL)),
                ("LINEBELOW", (0, 0), (-1, 0), 1.2, _hex(self.styles.accent)),
            ]
        offset = 1 if has_header else 0
        for index, color in enumerate(block.row_colors):
            if color and index + offset < row_count:
                row = index + offset
                commands.append(("LINEBEFORE", (0, row), (0, row), 2.5, _hex(color)))
        return TableStyle(commands)

    def _rule(self, block: Rule) -> list[Flowable]:
        return [
            HRFlowable(
                width="100%",
                thickness=0.6,
                color=_hex(theme.RULE),
                spaceBefore=4,
                spaceAfter=8,
            )
        ]

    def _code(self, block: CodeBlock) -> list[Flowable]:
        text = escape(block.text).replace("\n", "<br/>").replace("  ", "&nbsp; ")
        return [RLParagraph(text, self.styles.code), Spacer(1, 6)]

    def _stats(self, block: StatRow) -> list[Flowable]:
        cells = [
            [
                RLParagraph(escape(stat.label), self.styles.stat_label),
                RLParagraph(escape(stat.value), self.styles.stat_value),
            ]
            for stat in block.stats
        ]
        count = max(len(cells), 1)
        table = RLTable(
            [cells], colWidths=[FRAME_WIDTH / count] * count, cornerRadii=[5, 5, 5, 5]
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _hex(theme.TABLE_HEADER_FILL)),
                    ("BOX", (0, 0), (-1, -1), 0.6, _hex(theme.HAIRLINE)),
                    ("LINEAFTER", (0, 0), (-2, -1), 0.6, _hex(theme.HAIRLINE)),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        return [table, Spacer(1, 12)]

    def _callout(self, block: Callout) -> list[Flowable]:
        fill, border, text = theme.CALLOUT_COLORS[block.tone]
        content: list[Flowable] = []
        if block.title:
            title_style = self.styles._style(
                "callout-title", fontName=theme.FONT_SEMIBOLD, textColor=_hex(text)
            )
            content.append(RLParagraph(escape(block.title), title_style))
        content += self.flowables(block.body)
        table = RLTable(
            [[content]],
            colWidths=[FRAME_WIDTH],
            hAlign="LEFT",
            cornerRadii=[4, 4, 4, 4],
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _hex(fill)),
                    ("LINEBEFORE", (0, 0), (0, 0), 2.5, _hex(border)),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        return [table, Spacer(1, 10)]

    def _chart(self, block: ChartBlock) -> list[Flowable]:
        return [Spacer(1, 2), ChartFlowable(block.chart), Spacer(1, 12)]

    def _image(self, block: ImageBlock) -> list[Flowable]:
        # Under its caption's indent, in a hairline frame so a white slide
        # keeps its edge on the page. The caption stays on the picture's page,
        # so a capture never starts a page with a picture whose time is on the
        # page before.
        indent = self.styles.quote.leftIndent
        width, height = block.size_pt(FRAME_WIDTH - indent)
        framed = RLTable(
            [[RLImage(BytesIO(block.data), width=width, height=height)]],
            colWidths=[width],
            rowHeights=[height],
        )
        framed.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, _hex(theme.RULE)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        framed.hAlign = "LEFT"
        return [
            KeepTogether(
                [
                    self._para(block.caption, self.styles.quote),
                    Indenter(left=indent),
                    framed,
                    Indenter(left=-indent),
                    Spacer(1, 10),
                ]
            )
        ]

    def _transcript(self, block: TranscriptLine) -> list[Flowable]:
        stamp = (
            f'<font face="{theme.FONT_MONO}" size="7.8" color="{theme.FAINT}">'
            f"{format_transcript_time(block.start_seconds)}</font>"
        )
        name = f"<b>{escape(block.speaker)}</b>"
        if block.color:
            name = f'<font color="{theme.readable_on_white(block.color)}">{name}</font>'
        return [
            RLParagraph(
                f"{stamp}&nbsp;&nbsp;{name}&nbsp;&nbsp;{escape(block.text)}",
                self.styles.transcript,
            )
        ]


# -- page furniture ---------------------------------------------------------------------


class _Letterhead:
    """Draws the header and footer on each page."""

    def __init__(self, document: ExportDocument) -> None:
        self.document = document
        self.accent = theme.accent(document.branded)

    def first_page(self, canvas: Canvas, doc: Any) -> None:
        canvas.saveState()
        top = HEADER_TOP
        if self.document.branded:
            canvas.drawImage(
                str(theme.LOGO_PATH), MARGIN_X, top - 22, 22, 22, mask="auto"
            )
            canvas.setFont(theme.FONT_SEMIBOLD, 16)
            canvas.setFillColor(_hex(theme.FOREGROUND))
            canvas.drawString(MARGIN_X + 29, top - 16.5, "Nojoin")
        right = PAGE_WIDTH - MARGIN_X
        canvas.setFont(theme.FONT_SEMIBOLD, 6.8)
        canvas.setFillColor(_hex(theme.HELPER))
        canvas.drawRightString(right, top - 8, "MEETING RECORD")
        canvas.setFont(theme.FONT_REGULAR, 9.5)
        canvas.setFillColor(_hex(theme.MUTED))
        canvas.drawRightString(right, top - 20, self.document.dated)
        canvas.setStrokeColor(_hex(self.accent))
        canvas.setLineWidth(1.6)
        canvas.line(MARGIN_X, top - 31, right, top - 31)
        self._footer(canvas)
        canvas.restoreState()

    def later_page(self, canvas: Canvas, doc: Any) -> None:
        canvas.saveState()
        top = HEADER_TOP
        x = MARGIN_X
        if self.document.branded:
            canvas.drawImage(str(theme.LOGO_PATH), x, top - 12, 12, 12, mask="auto")
            x += 17
        right = PAGE_WIDTH - MARGIN_X
        canvas.setFont(theme.FONT_REGULAR, 8)
        canvas.setFillColor(_hex(theme.HELPER))
        title = fit_text(self.document.title, theme.FONT_REGULAR, 8, right - x - 90)
        canvas.drawString(x, top - 9, title)
        canvas.drawRightString(right, top - 9, self.document.dated)
        canvas.setStrokeColor(_hex(theme.HAIRLINE))
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN_X, top - 16, right, top - 16)
        self._footer(canvas)
        canvas.restoreState()

    def _footer(self, canvas: Canvas) -> None:
        right = PAGE_WIDTH - MARGIN_X
        canvas.setStrokeColor(_hex(theme.HAIRLINE))
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN_X, FOOTER_BASELINE + 9, right, FOOTER_BASELINE + 9)
        if not self.document.branded:
            return
        label = f"{theme.GENERATED_BY} · "
        canvas.setFont(theme.FONT_REGULAR, 7.5)
        canvas.setFillColor(_hex(theme.HELPER))
        canvas.drawString(MARGIN_X, FOOTER_BASELINE, label)
        link_x = MARGIN_X + theme.text_width(label, theme.FONT_REGULAR, 7.5)
        canvas.setFillColor(_hex(self.accent))
        canvas.drawString(link_x, FOOTER_BASELINE, theme.SITE_LABEL)
        link_width = theme.text_width(theme.SITE_LABEL, theme.FONT_REGULAR, 7.5)
        canvas.linkURL(
            theme.SITE_URL,
            (link_x, FOOTER_BASELINE - 2, link_x + link_width, FOOTER_BASELINE + 8),
            relative=0,
        )


class _NumberedCanvas(Canvas):
    """Defers drawing 'Page n of N' until the page count is known."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._page_states: list[dict[str, Any]] = []

    def showPage(self) -> None:
        self._page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._page_states)
        for state in self._page_states:
            self.__dict__.update(state)
            self.setFont(theme.FONT_REGULAR, 7.5)
            self.setFillColor(_hex(theme.HELPER))
            self.drawRightString(
                PAGE_WIDTH - MARGIN_X,
                FOOTER_BASELINE,
                f"Page {self._pageNumber} of {total}",
            )
            super().showPage()
        super().save()


def _title_block(document: ExportDocument, styles: _Styles) -> list[Flowable]:
    meta = {item.label: item.value for item in document.meta}
    first_line = "  ·  ".join(
        value for key in ("Date", "Duration") if (value := meta.get(key))
    )
    flowables: list[Flowable] = [
        RLParagraph(escape(document.title), styles.title),
        RLParagraph(escape(first_line), styles.meta),
    ]
    if meta.get("Speakers"):
        flowables.append(
            RLParagraph(
                f"<b>Speakers</b>&nbsp;&nbsp;{escape(meta['Speakers'])}", styles.meta
            )
        )
    flowables += [
        Spacer(1, 8),
        HRFlowable(
            width="100%", thickness=0.6, color=_hex(theme.HAIRLINE), spaceAfter=14
        ),
    ]
    return flowables


def _section_flowables(section: Section, builder: _StoryBuilder) -> list[Flowable]:
    styles = builder.styles
    return [
        RLParagraph(escape(section.title), styles.section),
        HRFlowable(
            width=28,
            thickness=2,
            color=_hex(styles.accent),
            hAlign="LEFT",
            spaceBefore=3,
            spaceAfter=12,
        ),
        *builder.flowables(section.blocks),
    ]


def render_pdf(document: ExportDocument) -> bytes:
    theme.register_fonts()
    styles = _Styles(theme.accent(document.branded))
    builder = _StoryBuilder(styles)
    letterhead = _Letterhead(document)

    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=LATER_PAGE_TOP,
        bottomMargin=BOTTOM_MARGIN,
        title=document.title,
        author="Nojoin" if document.branded else "",
        creator="Nojoin" if document.branded else "",
    )
    frame_height_first = PAGE_HEIGHT - FIRST_PAGE_TOP - BOTTOM_MARGIN
    frame_height_later = PAGE_HEIGHT - LATER_PAGE_TOP - BOTTOM_MARGIN
    doc.addPageTemplates(
        [
            PageTemplate(
                id="first",
                frames=[
                    Frame(
                        MARGIN_X,
                        BOTTOM_MARGIN,
                        FRAME_WIDTH,
                        frame_height_first,
                        id="first",
                        leftPadding=0,
                        rightPadding=0,
                        topPadding=0,
                        bottomPadding=0,
                    )
                ],
                onPage=letterhead.first_page,
            ),
            PageTemplate(
                id="later",
                frames=[
                    Frame(
                        MARGIN_X,
                        BOTTOM_MARGIN,
                        FRAME_WIDTH,
                        frame_height_later,
                        id="later",
                        leftPadding=0,
                        rightPadding=0,
                        topPadding=0,
                        bottomPadding=0,
                    )
                ],
                onPage=letterhead.later_page,
            ),
        ]
    )

    story: list[Flowable] = [NextPageTemplate("later"), *_title_block(document, styles)]
    for index, section in enumerate(document.sections):
        if index:
            story.append(PageBreak())
        story += _section_flowables(section, builder)
    doc.build(story, canvasmaker=_NumberedCanvas)
    return buffer.getvalue()
