"""Carry confirmed phrase ends across changed timing and word tokenisation."""

from collections.abc import Sequence
from typing import Any

from backend.utils.live_word_settling import _groups, word_signature

from .live_settling_lines import word_extent

Word = dict[str, Any]


def _centre(word: Word) -> float | None:
    extent = word_extent(word)
    return (extent[0] + extent[1]) / 2 if extent is not None else None


def _word_group(words: Sequence[Word], index: int) -> tuple[Word, int]:
    """Use reconciliation's anchor and complete owned span for an unplaced token."""
    for anchor, indices in _groups(words):
        if index in indices:
            return anchor, indices[-1]
    return words[index], index


def _phrase_spans(words: Sequence[Word]) -> tuple[str, dict[int, int], dict[int, int]]:
    """Index lexical offsets at complete token edges, including punctuation."""
    text = ""
    starts: dict[int, int] = {}
    ends: dict[int, int] = {}
    grouped = {index for _, indices in _groups(words) for index in indices}
    for index, word in enumerate(words):
        if index not in grouped:
            # A wholly unaligned hypothesis owns no neighbouring timed words.
            text += "\0"
            continue
        starts.setdefault(len(text), index)
        text += word_signature(str(word.get("word") or ""))
        ends[len(text)] = index
    return text, starts, ends


def _retokenised_end(
    previous: Sequence[Word], current: Sequence[Word], after_index: int
) -> int | None:
    """Match lexical suffixes across whole token edges within temporal tolerance."""
    old_centre = _centre(_word_group(previous, len(previous) - 1)[0])
    if old_centre is None:
        return None
    text, starts, ends = _phrase_spans(current)
    suffix = ""
    candidates: list[tuple[int, float, int]] = []
    for word in reversed(previous):
        suffix = word_signature(str(word.get("word") or "")) + suffix
        if not suffix:
            continue
        offset = text.find(suffix)
        while offset >= 0:
            index = ends.get(offset + len(suffix))
            if offset in starts and starts[offset] > after_index and index is not None:
                centre = _centre(_word_group(current, index)[0])
                if centre is not None and abs(centre - old_centre) <= 1000:
                    candidates.append((len(suffix), -abs(centre - old_centre), index))
            offset = text.find(suffix, offset + 1)
    return max(candidates)[2] if candidates else None


def remap_phrase_end(
    previous: Sequence[Word], current: Sequence[Word], *, after_index: int = -1
) -> int | None:
    """Find the final current token belonging to a previously closed phrase.

    Reconciliation can reuse the terminal UUID for the first token of a split.
    Only unchanged lexical identity can use that shortcut. Otherwise compare
    whole lexical suffixes before accepting the reused UUID for changed wording.
    """
    if not previous:
        return None
    terminal = previous[-1]
    signature = word_signature(str(terminal.get("word") or ""))
    reused_end = None
    for index, word in enumerate(current):
        if index > after_index and word.get("id") == terminal.get("id"):
            reused_end = index
            if word_signature(str(word.get("word") or "")) == signature:
                return _word_group(current, index)[1]
    retokenised = _retokenised_end(previous, current, after_index)
    end = retokenised if retokenised is not None else reused_end
    return _word_group(current, end)[1] if end is not None else None
