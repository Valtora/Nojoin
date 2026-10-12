"""A transcript cut into consecutive parts, each under a byte limit.

Used by long-meeting notes (``backend.utils.long_meeting_notes``) to fit a
meeting into a model's context window a part at a time.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional, Sequence


def utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


# Both notes transcripts write one utterance per line, opening with its times:
# "[MM:SS - MM:SS] Name: text" (Regenerate Notes) or "[HH:MM:SS - HH:MM:SS]"
# (the automatic pipeline). Minutes past an hour stay in the MM field.
TIMESTAMP_PATTERN = r"\d+:\d{2}(?::\d{2})?"
_LINE_SPAN = re.compile(rf"^\[({TIMESTAMP_PATTERN})(?:\s*-\s*({TIMESTAMP_PATTERN}))?\]")
_LINE_PREFIX = re.compile(r"^\[[^\]\n]*\][^:\n]*:\s*")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class TranscriptPart:
    """Consecutive transcript lines, with the time the first starts and the last ends."""

    text: str
    start: Optional[str]
    end: Optional[str]

    @property
    def size(self) -> int:
        return utf8_size(self.text)


def _part(lines: Sequence[str]) -> TranscriptPart:
    first = _LINE_SPAN.match(lines[0])
    last = _LINE_SPAN.match(lines[-1])
    return TranscriptPart(
        text="\n".join(lines),
        start=first.group(1) if first else None,
        end=(last.group(2) or last.group(1)) if last else None,
    )


def _hard_cut(text: str, limit: int) -> list[str]:
    """``text`` in pieces of at most ``limit`` bytes, cut between characters."""
    pieces: list[str] = []
    current, size = "", 0
    for char in text:
        width = utf8_size(char)
        if current and size + width > limit:
            pieces.append(current)
            current, size = "", 0
        current += char
        size += width
    if current:
        pieces.append(current)
    return pieces


def _units(body: str, limit: int) -> list[str]:
    """Sentences of ``body``, with any over ``limit`` bytes broken into words."""
    units: list[str] = []
    for sentence in _SENTENCE_BREAK.split(body):
        if utf8_size(sentence) <= limit:
            units.append(sentence)
            continue
        for word in sentence.split():
            units.extend([word] if utf8_size(word) <= limit else _hard_cut(word, limit))
    return units


def _split_long_line(line: str, limit: int) -> list[str]:
    """One utterance too long for a part, as lines of at most ``limit`` bytes.

    Cuts fall between sentences where possible, else between words. Every
    piece keeps the line's timestamp and speaker, so each still says who spoke
    and when.
    """
    match = _LINE_PREFIX.match(line)
    prefix = match.group(0) if match else ""
    room = limit - utf8_size(prefix)
    if room < limit // 2:
        # A prefix this long is not a timestamp and a speaker name.
        prefix, room = "", limit
    pieces: list[str] = []
    current = ""
    for unit in _units(line[len(prefix) :], room):
        candidate = f"{current} {unit}" if current else unit
        if current and utf8_size(candidate) > room:
            pieces.append(current)
            current = unit
        else:
            current = candidate
    if current:
        pieces.append(current)
    return [prefix + piece for piece in pieces]


def split_transcript(transcript: str, part_limit: int) -> list[TranscriptPart]:
    """Consecutive parts of at most ``part_limit`` UTF-8 bytes, cut between lines.

    Every line is one utterance, so no cut falls inside one unless it is too
    long for a part by itself. The count is the fewest parts that fit, and at
    least two, since a transcript is only split after it failed whole. Each
    part runs to about an equal share, so the last is not a short tail.
    """
    lines: list[str] = []
    for line in transcript.splitlines():
        if not line.strip():
            continue
        if utf8_size(line) + 1 > part_limit:
            lines.extend(_split_long_line(line, part_limit - 1))
        else:
            lines.append(line)
    if not lines:
        return []

    total = sum(utf8_size(line) + 1 for line in lines)
    share = total / max(2, math.ceil(total / part_limit))
    groups: list[list[str]] = [[]]
    size = 0
    for line in lines:
        cost = utf8_size(line) + 1
        # Cut before a line that would overrun the part, or whose middle would
        # fall past this part's share.
        if groups[-1] and (size + cost > part_limit or size + cost / 2 > share):
            groups.append([])
            size = 0
        groups[-1].append(line)
        size += cost
    return [_part(group) for group in groups]
