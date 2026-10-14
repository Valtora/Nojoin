"""Embed Geist in a Word document so it reads in the brand type everywhere.

Almost nobody opening an export has Geist installed, and Word substitutes a
font it lacks with whatever it picks, so without embedding the document only
looks right on the machine that made it. python-docx has no API for embedded
fonts, so this writes the parts ECMA-376 Part 1, section 17.8.1 describes:

- each face as an obfuscated font part related from the font table, with the
  first 32 bytes XORed with a per-font GUID key;
- a ``w:font`` entry in the font table naming those parts;
- ``w:embedTrueTypeFonts`` in the settings, so Word keeps the fonts when the
  reader saves the file again.

Only Regular and Bold are embedded. They are what headings and body text use,
Word draws italics from the regular face when the italic is absent, and each
extra face adds about 130 KB to every file.
"""

from __future__ import annotations

import uuid
from typing import Any

from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml.ns import qn
from lxml import etree

from . import theme

OBFUSCATED_FONT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.obfuscatedFont"
)
FONT_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"
)

# Elements that must precede w:embedTrueTypeFonts in CT_Settings.
_SETTINGS_BEFORE_EMBED = (
    "w:writeProtection",
    "w:view",
    "w:zoom",
    "w:removePersonalInformation",
    "w:removeDateAndTime",
    "w:doNotDisplayPageBoundaries",
    "w:displayBackgroundShape",
    "w:printPostScriptOverText",
    "w:printFractionalCharacterWidth",
    "w:printFormsData",
)


def obfuscate(font: bytes, key: uuid.UUID) -> bytes:
    """XOR the font's first 32 bytes with the key, as Word expects.

    The key's bytes are taken from its hex string in reverse order, which is
    how Word and LibreOffice read the ``w:fontKey`` attribute back.
    """
    key_bytes = bytes.fromhex(key.hex)[::-1]
    data = bytearray(font)
    for index in range(min(32, len(data))):
        data[index] ^= key_bytes[index % 16]
    return bytes(data)


def _font_table_part(document: Any) -> Any:
    for relationship in document.part.rels.values():
        if relationship.reltype == RT.FONT_TABLE:
            return relationship.target_part
    return None


def _embed_face(
    font_table: Any, package: Any, face: str, index: int
) -> tuple[str, str]:
    key = uuid.uuid4()
    part = Part(
        PackURI(f"/word/fonts/font{index}.odttf"),
        OBFUSCATED_FONT_CONTENT_TYPE,
        obfuscate(theme.font_path(face).read_bytes(), key),
        package,
    )
    relationship_id = font_table.relate_to(part, FONT_RELATIONSHIP)
    return relationship_id, "{" + str(key).upper() + "}"


def _font_entry(name: str, embeds: list[tuple[str, str, str]]) -> Any:
    font = etree.Element(qn("w:font"))
    font.set(qn("w:name"), name)
    # Read by Word when it cannot load the embedded face: a sans-serif stand-in
    # rather than the default serif.
    for tag, value in (
        ("w:altName", "Arial"),
        ("w:charset", "00"),
        ("w:family", "swiss"),
        ("w:pitch", "variable"),
    ):
        child = etree.SubElement(font, qn(tag))
        child.set(qn("w:val"), value)
    for tag, relationship_id, font_key in embeds:
        child = etree.SubElement(font, qn(tag))
        child.set(qn("r:id"), relationship_id)
        child.set(qn("w:fontKey"), font_key)
    return font


def _enable_embedding_setting(document: Any) -> None:
    settings = document.settings.element
    if settings.find(qn("w:embedTrueTypeFonts")) is not None:
        return
    element = etree.Element(qn("w:embedTrueTypeFonts"))
    anchor = None
    for tag in _SETTINGS_BEFORE_EMBED:
        found = settings.find(qn(tag))
        if found is not None:
            anchor = found
    if anchor is None:
        settings.insert(0, element)
    else:
        anchor.addnext(element)


def embed_geist(document: Any) -> bool:
    """Embed Geist Regular and Bold; False if the template has no font table."""
    font_table = _font_table_part(document)
    if font_table is None:
        return False
    package = document.part.package
    embeds = []
    for index, (tag, face) in enumerate(
        (("w:embedRegular", theme.FONT_REGULAR), ("w:embedBold", theme.FONT_BOLD)),
        start=1,
    ):
        relationship_id, font_key = _embed_face(font_table, package, face, index)
        embeds.append((tag, relationship_id, font_key))

    root = etree.fromstring(font_table.blob)
    for existing in root.findall(qn("w:font")):
        if existing.get(qn("w:name")) == theme.FONT_REGULAR:
            root.remove(existing)
    root.append(_font_entry(theme.FONT_REGULAR, embeds))
    font_table._blob = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    _enable_embedding_setting(document)
    return True
