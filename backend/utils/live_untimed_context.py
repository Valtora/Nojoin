"""Trim verified locked lead-up from unaligned text without rewriting its tail."""

import re
from collections.abc import Sequence
from typing import Any

from backend.utils.word_text import reconstruct_text_from_words

Word = dict[str, Any]
_LEXICAL_TOKEN = re.compile(r"\w+(?:['’]\w+)*")


def _context_end(text: str, context: str) -> int:
    """Locate the longest context suffix matching the hypothesis's lexical prefix."""
    spans = list(_LEXICAL_TOKEN.finditer(text))
    incoming = [span.group().lower().replace("’", "'") for span in spans]
    previous = [
        span.group().lower().replace("’", "'")
        for span in _LEXICAL_TOKEN.finditer(context)
    ]
    for count in range(min(len(incoming), len(previous)), 0, -1):
        if incoming[:count] != previous[-count:]:
            continue
        if count == len(spans):
            return len(text)
        end, following = spans[count - 1].end(), spans[count].start()
        # Closing punctuation belongs to the removed word; keep opening quotes
        # after its separator and preserve all characters within the new tail.
        separator = next(
            (index for index in range(end, following) if text[index].isspace()), end
        )
        return separator + len(text[separator:]) - len(text[separator:].lstrip())
    return 0


def untimed_tail_atom(decoded: Sequence[Word], context: str) -> Word | None:
    """Build one exact untimed atom, retaining source offsets for a single source.

    A composite of distinct word payloads has its own span coordinate system;
    it cannot truthfully inherit the first source's identity.
    """
    single = len(decoded) == 1
    text = (
        str(decoded[0].get("word") or "")
        if single
        else reconstruct_text_from_words(list(decoded))
    )
    removed = _context_end(text, context)
    if removed == len(text):
        return None
    atom = {**decoded[0], "word": text[removed:], "start_ms": None, "end_ms": None}
    source_start = int(decoded[0].get("source_start") or 0) if single else 0
    atom.update(
        source_start=source_start + removed, source_end=source_start + len(text)
    )
    if not single:
        atom.pop("source_id", None)
    # This is a new phrase after immutable context, even when its original
    # source span starts after zero and would otherwise imply concatenation.
    atom["join_left"] = False
    return atom
