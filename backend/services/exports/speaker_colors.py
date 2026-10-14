"""Each speaker's colour in an export, matching what the reader sees in the app.

A meeting's automatically assigned speaker colours exist only in the browser:
``buildMeetingSpeakerColors`` computes them from the transcript, and the
server stores a colour only once a user picks one. Rather than port that
assignment and risk the two drifting, the export dialog sends the colours it is
already showing, keyed by diarisation label, and this module resolves them.

A caller with no colours to send (an API client) still gets one colour per
person throughout the file: a speaker's saved colour where there is one, and
otherwise the chart fallback slots by order of first appearance, folding into
the neutral "other" colour after the eighth rather than repeating a hue.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from . import theme

MAX_CLIENT_COLORS = 256
_MAX_KEY_LENGTH = 200


def palette_hex(value: object) -> str | None:
    """The hex for a palette key such as ``"blue"``, or None for anything else."""
    if not isinstance(value, str):
        return None
    return theme.SPEAKER_PALETTE.get(value.strip().lower())


def parse_client_colors(raw: str | None) -> dict[str, str]:
    """The dialog's speaker colours, keeping only well-formed palette entries.

    Malformed input is ignored rather than refused: colours are cosmetic, and a
    bad value must not cost the user their export.
    """
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    colors: dict[str, str] = {}
    for key, value in list(decoded.items())[:MAX_CLIENT_COLORS]:
        hex_value = palette_hex(value)
        if isinstance(key, str) and len(key) <= _MAX_KEY_LENGTH and hex_value:
            colors[key] = hex_value
    return colors


def _slot(index: int) -> str:
    return (
        theme.CHART_SLOTS[index]
        if index < len(theme.CHART_SLOTS)
        else theme.CHART_OTHER
    )


def resolve_label_colors(
    labels: Iterable[str],
    saved: Mapping[str, str | None],
    client: Mapping[str, str],
) -> dict[str, str]:
    """A colour per diarisation label, in order of first appearance.

    ``saved`` maps a label to the palette key a user chose for that speaker;
    ``client`` maps a label to the hex the export dialog sent.
    """
    colors: dict[str, str] = {}
    fallback_index = 0
    for label in labels:
        if label in colors:
            continue
        chosen = client.get(label) or palette_hex(saved.get(label))
        if chosen is None:
            chosen = _slot(fallback_index)
            fallback_index += 1
        colors[label] = chosen
    return colors


def analytics_speaker_colors(
    speakers: Sequence[Mapping[str, Any]],
    label_colors: Mapping[str, str],
) -> dict[str, str]:
    """Analytics speakers, keyed by speaker key, in their transcript colours."""
    colors: dict[str, str] = {}
    for index, speaker in enumerate(speakers):
        label = speaker.get("diarization_label")
        colors[speaker["speaker_key"]] = (
            label_colors.get(label or "")
            or palette_hex(speaker.get("color"))
            or (theme.CHART_OTHER if label is None else _slot(index))
        )
    return colors
