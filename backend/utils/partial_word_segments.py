"""Keep untimed words with their timed neighbours, without inventing bounds."""

from typing import Any

from backend.utils.word_times import has_untimed_words, has_word_timing


def timed_word_groups(
    words: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Attach unknown words to the preceding anchor; leading ones to the first."""
    groups: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    leading: list[dict[str, Any]] = []
    for word in words:
        if has_word_timing(word):
            groups.append((word, [*leading, word]))
            leading = []
        elif groups:
            groups[-1][1].append(word)
        else:
            leading.append(word)
    return groups


def normalise_partial_word_segments(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Use observed segment bounds; no retained timings means window failure."""
    result = []
    for segment in segments:
        if not has_untimed_words(segment):
            result.append(segment)
            continue
        timed = [word for word in segment["words"] if has_word_timing(word)]
        if timed:
            result.append(
                {**segment, "start": timed[0]["start"], "end": timed[-1]["end"]}
            )
        else:
            result.append(
                {**segment, "words": [], "alignment_error": "No usable word timings"}
            )
    return result
