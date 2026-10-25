"""Small pieces of WordprocessingML that python-docx has no API for.

Borders, shading, header-row repetition, page fields, hyperlinks, restartable
numbering and theme-free font names. Each helper writes the element ECMA-376
names and nothing more, so the renderer reads as layout rather than XML.
"""

from __future__ import annotations

from typing import Any

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

HYPERLINK_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)

# CT_TblPr children that must precede w:tblBorders, w:shd and w:tblCellMar.
_BORDER_SIDES = ("top", "left", "bottom", "right", "insideH", "insideV")


def _hex(value: str) -> str:
    return value.lstrip("#").upper()


def set_font_name(properties_owner: Any, name: str) -> None:
    """Name a font for every script and drop any theme font that would win.

    The default template's styles use theme fonts (``w:asciiTheme``), and a
    theme attribute takes precedence over a named font, so setting the name
    alone leaves headings in Calibri Light.
    """
    r_pr = properties_owner.get_or_add_rPr()
    fonts = r_pr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, fonts)
    for attribute in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        fonts.attrib.pop(qn(attribute), None)
    for attribute in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attribute), name)


def clear_theme_color(r_pr: Any) -> None:
    color = r_pr.find(qn("w:color"))
    if color is not None:
        for attribute in ("w:themeColor", "w:themeShade", "w:themeTint"):
            color.attrib.pop(qn(attribute), None)


def _border(tag: str, *, size: int, color: str, val: str = "single") -> Any:
    element = OxmlElement(tag)
    element.set(qn("w:val"), val)
    element.set(qn("w:sz"), str(size))
    element.set(qn("w:space"), "0")
    element.set(qn("w:color"), _hex(color) if val != "nil" else "auto")
    return element


def set_table_borders(table: Any, borders: dict[str, tuple[int, str] | None]) -> None:
    """Set table-level borders; None means no border on that side."""
    tbl_pr = table._tbl.tblPr
    existing = tbl_pr.find(qn("w:tblBorders"))
    if existing is not None:
        tbl_pr.remove(existing)
    element = OxmlElement("w:tblBorders")
    for side in _BORDER_SIDES:
        spec = borders.get(side)
        if spec is None:
            element.append(_border(f"w:{side}", size=0, color="", val="nil"))
        else:
            element.append(_border(f"w:{side}", size=spec[0], color=spec[1]))
    # Order inside tblPr matters to Word: borders sit after the style, width,
    # justification and indentation, before shading, layout and cell margins.
    anchor = None
    for tag in (
        "w:tblStyle",
        "w:tblpPr",
        "w:tblOverlap",
        "w:bidiVisual",
        "w:tblStyleRowBandSize",
        "w:tblStyleColBandSize",
        "w:tblW",
        "w:jc",
        "w:tblCellSpacing",
        "w:tblInd",
    ):
        found = tbl_pr.find(qn(tag))
        if found is not None:
            anchor = found
    if anchor is None:
        tbl_pr.insert(0, element)
    else:
        anchor.addnext(element)


def set_grid_widths(table: Any, widths_pt: list[float]) -> None:
    """The column grid, which LibreOffice lays out from rather than cell widths."""
    for column, width in zip(table._tbl.tblGrid.findall(qn("w:gridCol")), widths_pt):
        column.set(qn("w:w"), str(round(width * 20)))


def set_table_cell_margins(
    table: Any, *, top: int, bottom: int, left: int, right: int
) -> None:
    """Default cell margins for the table, in twentieths of a point."""
    tbl_pr = table._tbl.tblPr
    existing = tbl_pr.find(qn("w:tblCellMar"))
    if existing is not None:
        tbl_pr.remove(existing)
    margins = OxmlElement("w:tblCellMar")
    for side, value in (
        ("top", top),
        ("left", left),
        ("bottom", bottom),
        ("right", right),
    ):
        element = OxmlElement(f"w:{side}")
        element.set(qn("w:w"), str(value))
        element.set(qn("w:type"), "dxa")
        margins.append(element)
    tbl_pr.append(margins)


def _tc_pr_child(cell: Any, tag: str) -> Any:
    tc_pr = cell._tc.get_or_add_tcPr()
    existing = tc_pr.find(qn(tag))
    if existing is not None:
        tc_pr.remove(existing)
    element = OxmlElement(tag)
    # tcPr children order: tcW, gridSpan, vMerge, tcBorders, shd, ...
    if tag == "w:tcBorders":
        anchor = tc_pr.find(qn("w:tcW"))
        if anchor is not None:
            anchor.addnext(element)
            return element
        tc_pr.insert(0, element)
        return element
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is not None:
        borders.addnext(element)
    else:
        width = tc_pr.find(qn("w:tcW"))
        if width is not None:
            width.addnext(element)
        else:
            tc_pr.insert(0, element)
    return element


def shade_cell(cell: Any, fill: str) -> None:
    shading = _tc_pr_child(cell, "w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), _hex(fill))


def set_cell_borders(cell: Any, borders: dict[str, tuple[int, str]]) -> None:
    element = _tc_pr_child(cell, "w:tcBorders")
    for side in ("top", "left", "bottom", "right"):
        if side in borders:
            size, color = borders[side]
            element.append(_border(f"w:{side}", size=size, color=color))


def repeat_as_header(row: Any) -> None:
    """Mark the row as a header so Word repeats it across page breaks."""
    tr_pr = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    tr_pr.append(header)


def keep_row_together(row: Any) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement("w:cantSplit"))


def paragraph_border(
    paragraph: Any, side: str, *, size: int, color: str, space: int = 4
) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        p_pr.append(borders)
    element = _border(f"w:{side}", size=size, color=color)
    element.set(qn("w:space"), str(space))
    borders.append(element)


def add_field(paragraph: Any, instruction: str, placeholder: str) -> Any:
    """A simple field such as PAGE or NUMPAGES; Word fills it when it lays out."""
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), instruction)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = placeholder
    run.append(text)
    field.append(run)
    paragraph._p.append(field)
    return run


def add_hyperlink(paragraph: Any, url: str) -> Any:
    """An empty external hyperlink in the paragraph; the caller adds its runs.

    Returns the ``w:hyperlink`` element, or None when the URL cannot be related,
    so a malformed link costs only its link and never the export.
    """
    try:
        relationship_id = paragraph.part.relate_to(
            url, HYPERLINK_RELATIONSHIP, is_external=True
        )
    except (ValueError, TypeError):
        return None
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    paragraph._p.append(hyperlink)
    return hyperlink


def restart_numbering(document: Any, style_name: str) -> int | None:
    """A fresh numbering instance for one list, so each list counts from 1.

    Every paragraph in a numbered list style shares one numbering instance by
    default, which makes a second list carry on from the first. A new
    instance over the same abstract definition, with its start overridden,
    gives a list its own count while keeping the style's look.
    """
    try:
        style = document.styles[style_name]
        numbering = document.part.numbering_part.element
    except (KeyError, NotImplementedError):
        return None
    num_pr = style.element.pPr.numPr if style.element.pPr is not None else None
    if num_pr is None or num_pr.numId is None:
        return None
    current = numbering.num_having_numId(num_pr.numId.val)
    new_num = numbering.add_num(current.abstractNumId.val)
    new_num.add_lvlOverride(ilvl=0).add_startOverride(1)
    return int(new_num.numId)


def apply_numbering(paragraph: Any, num_id: int, level: int = 0) -> None:
    num_pr = paragraph._p.get_or_add_pPr().get_or_add_numPr()
    num_pr.get_or_add_ilvl().val = level
    num_pr.get_or_add_numId().val = num_id


def picture_alt_text(paragraph: Any, text: str) -> None:
    """Name the paragraph's pictures for a screen reader (Word's Alt Text)."""
    for doc_pr in paragraph._p.iter(qn("wp:docPr")):
        doc_pr.set("descr", text)


def picture_outline(paragraph: Any, color: str, width_pt: float = 0.6) -> None:
    """A hairline round the paragraph's pictures, so a white slide keeps its edge."""
    for shape_properties in paragraph._p.iter(qn("pic:spPr")):
        line = OxmlElement("a:ln")
        line.set("w", str(round(width_pt * 12700)))
        fill = OxmlElement("a:solidFill")
        rgb = OxmlElement("a:srgbClr")
        rgb.set("val", _hex(color))
        fill.append(rgb)
        line.append(fill)
        shape_properties.append(line)
