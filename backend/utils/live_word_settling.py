"""Pure, bounded word reconciliation for a captured live settling policy.

Callers supply the affected tail and nearby locked anchors, never meeting-wide
history. Failed or skipped decodes do not call reconciliation. All bounds use
absolute milliseconds; null observed bounds remain null.
"""

import re
from collections.abc import Sequence
from math import isfinite
from typing import Any, NotRequired, TypedDict, Unpack
from uuid import uuid4

from backend.utils.live_untimed_context import untimed_tail_atom
from backend.utils.word_text import reconstruct_text_from_words

Word = dict[str, Any]


class WindowPolicy(TypedDict):
    """Absolute window bounds and optional retained context."""

    audio_end_ms: int
    window_ms: int
    lead_ms: NotRequired[int]
    frontier_ms: NotRequired[int]
    speech_start_ms: NotRequired[int]


class DecodePolicy(TypedDict):
    """Captured agreement policy and the accepted decode's provenance."""

    k: int
    decode_id: str
    window_start_ms: int
    audio_end_ms: int
    input_start_ms: NotRequired[int | None]


def word_signature(text: str) -> str:
    """Compare lexical wording while retaining its exact displayed span."""
    return re.sub(r"[^\w']", "", text.lower())


def _timed(word: Word) -> bool:
    start, end = word.get("start_ms"), word.get("end_ms")
    return (
        all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(value)
            for value in (start, end)
        )
        and end >= start
    )


def _groups(words: Sequence[Word]) -> list[tuple[Word, list[int]]]:
    """Attach untimed words to the preceding anchor, leading ones to first."""
    groups: list[tuple[Word, list[int]]] = []
    leading: list[int] = []
    previous_anchor = False
    for index, word in enumerate(words):
        if _timed(word):
            groups.append((word, [*leading, index]))
            leading = []
            previous_anchor = True
        elif "observed_audio_end_ms" in word:
            # A wholly unaligned hypothesis is its own protected atom, not an
            # unplaced token belonging to an older timed context word.
            previous_anchor = False
            leading = []
        elif previous_anchor:
            groups[-1][1].append(index)
        else:
            leading.append(index)
    return groups


def _centres(words: Sequence[Word]) -> list[float | None]:
    centres: list[float | None] = [None] * len(words)
    for anchor, indices in _groups(words):
        centre = (anchor["start_ms"] + anchor["end_ms"]) / 2
        for index in indices:
            centres[index] = centre
    return centres


def _compatible(first: float | None, second: float | None) -> bool:
    return first is not None and second is not None and abs(first - second) <= 1000


def _alignment(old: Sequence[Word], new: Sequence[Word]) -> list[tuple[int, int]]:
    """Monotone lexical LCS with the measured one-second temporal tolerance."""
    old_centres, new_centres = _centres(old), _centres(new)
    matches = [
        [
            _compatible(old_centres[i], new_centres[j])
            and word_signature(str(first.get("word") or ""))
            == word_signature(str(second.get("word") or ""))
            for j, second in enumerate(new)
        ]
        for i, first in enumerate(old)
    ]
    table = [[0] * (len(new) + 1) for _ in range(len(old) + 1)]
    for i in range(len(old) - 1, -1, -1):
        for j in range(len(new) - 1, -1, -1):
            table[i][j] = max(
                table[i + 1][j],
                table[i][j + 1],
                1 + table[i + 1][j + 1] if matches[i][j] else 0,
            )
    return _alignment_path(matches, table, len(old), len(new))


def _alignment_path(
    matches: list[list[bool]], table: list[list[int]], old_size: int, new_size: int
) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    i = j = 0
    while i < old_size and j < new_size:
        if matches[i][j] and table[i][j] == 1 + table[i + 1][j + 1]:
            result.append((i, j))
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return result


def _lock(word: Word, reason: str) -> Word:
    if word.get("locked"):
        return word
    return {**word, "locked": True, "lock_reason": reason}


def expire_word_state(words: Sequence[Word], *, window_start_ms: int) -> list[Word]:
    """Lock whole anchored groups as-is at the raw cap boundary, before decode.

    A negative boundary denotes a capture shorter than the window. Pass the raw
    audio-end-minus-window boundary, not the context-trimmed decode start.
    """
    expired = {
        index
        for anchor, indices in _groups(words)
        if anchor["start_ms"] <= window_start_ms
        for index in indices
    }
    expired.update(
        index
        for index, word in enumerate(words)
        if word.get(
            "first_observed_audio_end_ms",
            word.get("observed_audio_end_ms", float("inf")),
        )
        <= window_start_ms
    )
    return [
        _lock(word, "expiry") if index in expired else word
        for index, word in enumerate(words)
    ]


def close_word_state(words: Sequence[Word]) -> list[Word]:
    """Lock the last successful hypothesis without another observation."""
    return [_lock(word, "closure") for word in words]


def plan_settling_window(words: Sequence[Word], **policy: Unpack[WindowPolicy]) -> int:
    """Return the decode start, keeping lead-up inside the total input cap."""
    window_ms, lead_ms = policy["window_ms"], policy.get("lead_ms", 5000)
    if window_ms <= 0 or lead_ms < 0:
        raise ValueError("Window must be positive and lead-up nonnegative")
    boundary = policy["audio_end_ms"] - window_ms
    remaining = expire_word_state(words, window_start_ms=boundary)
    starts = [
        word["start_ms"]
        for word in remaining
        if _timed(word) and not word.get("locked")
    ]
    frontier = max(
        [
            policy.get("frontier_ms", 0),
            *(
                word["end_ms"]
                for word in remaining
                if _timed(word) and word.get("locked")
            ),
        ]
    )
    oldest = min(starts) if starts else max(frontier, policy.get("speech_start_ms", 0))
    untimed_frontier = max(
        (
            int(word["observed_audio_end_ms"])
            for word in remaining
            if word.get("locked")
            and not _timed(word)
            and "observed_audio_end_ms" in word
        ),
        default=0,
    )
    return max(0, boundary, oldest - lead_ms, untimed_frontier)


def _new_word(
    word: Word, previous: Word | None, *, equal: bool, decode_id: str
) -> Word:
    text = str(word.get("word") or "")
    result = {
        **word,
        "id": previous["id"] if previous and previous.get("id") else str(uuid4()),
        "locked": False,
        "unchanged_count": int(previous.get("unchanged_count", 0)) + 1
        if equal and previous
        else 0,
        "signature": word_signature(text),
        "last_decode_id": decode_id,
        "lock_reason": None,
    }
    result.setdefault("source_start", 0)
    result.setdefault("source_end", len(text))
    if "join_left" not in result and word.get("source_start", 0) > 0:
        result["join_left"] = bool(text) and not text[:1].isspace()
    return result


def _reconcile_gap(
    old: Sequence[Word],
    new: Sequence[Word],
    old_centres: Sequence[float | None],
    new_centres: Sequence[float | None],
    *,
    decode_id: str,
) -> list[Word]:
    result: list[Word] = []
    cursor = 0
    for index, word in enumerate(new):
        previous = None
        if cursor < len(old) and _compatible(old_centres[cursor], new_centres[index]):
            previous = old[cursor]
            cursor += 1
        result.append(_new_word(word, previous, equal=False, decode_id=decode_id))
    return result


def _reconcile_pending(
    old: Sequence[Word], new: Sequence[Word], decode_id: str
) -> list[Word]:
    result: list[Word] = []
    old_centres, new_centres = _centres(old), _centres(new)
    previous_i = previous_j = 0
    for i, j in [*_alignment(old, new), (len(old), len(new))]:
        result.extend(
            _reconcile_gap(
                old[previous_i:i],
                new[previous_j:j],
                old_centres[previous_i:i],
                new_centres[previous_j:j],
                decode_id=decode_id,
            )
        )
        if i < len(old):
            result.append(_new_word(new[j], old[i], equal=True, decode_id=decode_id))
        previous_i, previous_j = i + 1, j + 1
    return result


def _remove_locked_context(
    locked: Sequence[Word], decoded: Sequence[Word]
) -> list[Word]:
    if not locked:
        return list(decoded)
    frontier = max((word["end_ms"] for word in locked if _timed(word)), default=-1)
    groups = _groups(decoded)
    # Only overlapping groups can repeat locked context. A later repetition,
    # including an untimed neighbour, must not consume a locked lexical match.
    context_indices = [
        index
        for anchor, indices in groups
        if anchor["start_ms"] < frontier
        for index in indices
    ]
    context = [decoded[index] for index in context_indices]
    removed = {context_indices[j] for _, j in _alignment(locked, context)}
    centres = _centres(decoded)
    # An anchor owns its unplaced neighbours even when the word itself matched
    # locked context; never leave that neighbour behind as invented new text.
    for _, indices in groups:
        if any(index in removed for index in indices):
            removed.update(indices)
    return [
        word
        for index, word in enumerate(decoded)
        if index not in removed
        and centres[index] is not None
        and centres[index] > frontier
    ]


def _untimed_hypothesis(
    locked: Sequence[Word],
    pending: Sequence[Word],
    decoded: Sequence[Word],
    policy: DecodePolicy,
) -> list[Word]:
    """Keep failed alignment visible as one exact atom without invented times."""
    decode_id, audio_end_ms = policy["decode_id"], policy["audio_end_ms"]
    input_start_ms = policy.get("input_start_ms")
    # A missing input start conservatively retains the rolling-context assumption.
    # Disjoint input cannot contain these locks and may legitimately repeat them.
    context = [
        locked[index]
        for anchor, indices in _groups(locked)
        if input_start_ms is None or anchor["end_ms"] > input_start_ms
        for index in indices
    ]
    atom = untimed_tail_atom(decoded, reconstruct_text_from_words(context))
    if atom is None:
        return list(locked)
    previous = pending[0] if len(pending) == 1 and not _timed(pending[0]) else None
    current = _new_word(atom, previous, equal=False, decode_id=decode_id)
    first_seen = (
        previous.get(
            "first_observed_audio_end_ms",
            previous.get("observed_audio_end_ms", audio_end_ms),
        )
        if previous
        else audio_end_ms
    )
    current["first_observed_audio_end_ms"] = first_seen
    current["observed_audio_end_ms"] = audio_end_ms
    current["order"] = (
        int(previous.get("order", 0))
        if previous
        else max((int(word.get("order", -1)) for word in locked), default=-1) + 1
    )
    return [*locked, current]


def reconcile_word_state(
    existing_words: Sequence[Word],
    decoded_words: Sequence[Word],
    **policy: Unpack[DecodePolicy],
) -> list[Word]:
    """Accept one successful decode, retaining immutable locks and word identity.

    The publisher must additionally deduplicate accepted decode ids under its
    fence, including empty hypotheses. Unchanged comparisons begin at zero on
    first appearance. Only a mature prefix locks by agreement.
    """
    k, decode_id = policy["k"], policy["decode_id"]
    window_start_ms = policy["window_start_ms"]
    if k < 1 or not decode_id or window_start_ms > policy["audio_end_ms"]:
        raise ValueError("Invalid settling policy or decode identity")
    if any(word.get("last_decode_id") == decode_id for word in existing_words):
        return list(existing_words)
    expired = expire_word_state(existing_words, window_start_ms=window_start_ms)
    locked = [word for word in expired if word.get("locked")]
    pending = [word for word in expired if not word.get("locked")]
    if decoded_words and not _groups(decoded_words):
        locked_atom_end = max(
            (
                int(word["observed_audio_end_ms"])
                for word in locked
                if not _timed(word) and "observed_audio_end_ms" in word
            ),
            default=-1,
        )
        input_start = policy.get("input_start_ms")
        if locked_atom_end >= 0 and (
            input_start is None or input_start < locked_atom_end
        ):
            return expired
        return _untimed_hypothesis(locked, pending, decoded_words, policy)
    incoming = _remove_locked_context(locked, decoded_words)
    current = _reconcile_pending(pending, incoming, decode_id)
    first_order = max((int(word.get("order", -1)) for word in locked), default=-1) + 1
    if not locked and pending:
        first_order = min(int(word.get("order", 0)) for word in pending)
    current = [
        {**word, "order": first_order + index} for index, word in enumerate(current)
    ]
    current = expire_word_state(current, window_start_ms=window_start_ms)
    for _, indices in _groups(current):
        if any(
            not current[index].get("locked") and current[index]["unchanged_count"] < k
            for index in indices
        ):
            break
        for index in indices:
            current[index] = _lock(current[index], "agreement")
    return [*locked, *current]
