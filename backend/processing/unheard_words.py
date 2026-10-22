"""ASR words placed in digital silence: audio the engine read as exact zeros.

The final pass transcribes the VAD-muted copy of a recording, in which every
sample outside the speech VAD kept is an exact zero, and Whisper can still emit
a word or two there, most often "Thank you." (#508). A word whose span, with
UNHEARD_WORD_GUARD_S to spare on each side, lies inside one run of exact zeros in
the audio the engine read was not heard, so it is dropped before the merge.

The rule reads the zeros in that audio rather than the VAD stage's speech
regions. It therefore only ever touches audio the engine heard as silence,
including the regions under the VAD's minimum speech length that the mute step
zeroes and the muted first and last second. On unmuted input the only runs are
true digital silence, so it finds little or nothing, which is what unmuted
transcription needs: there, a word outside the VAD's regions is often speech
the VAD missed.

numpy and soundfile are imported inside the functions, like the engines' heavy
imports.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from backend.utils.word_text import reconstruct_text_from_words
from backend.utils.word_times import WORD_TIME_TOLERANCE_S, exceeds, falls_short

# Exact-zero audio, in seconds, that must lie on each side of a word, inside
# the same run, for the word to be dropped. Word times stray from the sound
# they mark: Whisper often starts a line's first word a quarter of a second or
# more into the silence before it, and Parakeet stamps a word up to two 80 ms
# frames after its onset. Replayed on a live library (#508), 0.5 s left every
# attributed line whole and dropped 96% of the "Thank you." lines invented in
# long silences. A smaller guard also dropped words abutting attributed speech,
# and a larger one changed little. A run shorter than twice the guard can hold
# no word that is dropped.
UNHEARD_WORD_GUARD_S = 0.5

# Frames read from the audio at a time, so a long recording is never held whole.
_BLOCK_FRAMES = 1 << 20

Run = tuple[float, float]


class UnheardWords(NamedTuple):
    """What remove_unheard_words removed from one result."""

    words: int
    segments: int


def digital_silence_runs(
    audio_path: str, min_run_s: float = 2 * UNHEARD_WORD_GUARD_S
) -> list[Run]:
    """(start, end) seconds of each run of exact-zero frames of ``min_run_s`` or more.

    A frame is silent when every channel is exactly zero. Runs are in time
    order and do not overlap. Raises what soundfile raises for an unreadable
    file (``RuntimeError`` or ``OSError``).
    """
    import soundfile

    with soundfile.SoundFile(audio_path) as audio:
        rate = audio.samplerate
        blocks = audio.blocks(blocksize=_BLOCK_FRAMES, dtype="float32", always_2d=True)
        frames = _zero_runs(blocks, max(1, round(min_run_s * rate)))
        return [(begin / rate, end / rate) for begin, end in frames]


def _zero_runs(blocks, min_frames: int) -> list[tuple[int, int]]:
    """(begin, end) frames of each all-zero run of ``min_frames`` or more.

    ``blocks`` are consecutive (frames, channels) arrays; a run may span them.
    """
    import numpy as np

    runs: list[tuple[int, int]] = []
    open_begin: int | None = None  # a run still open at the last block's end
    offset = 0
    for block in blocks:
        frames = np.asarray(block)
        silent = ~frames.any(axis=1)
        edges = np.diff(
            np.concatenate(([open_begin is not None], silent)).astype(np.int8)
        )
        begins = np.flatnonzero(edges == 1) + offset
        ends = np.flatnonzero(edges == -1) + offset
        if open_begin is not None and len(ends):
            runs.append((open_begin, int(ends[0])))
            ends = ends[1:]
            open_begin = None
        if len(begins) > len(ends):
            open_begin = int(begins[-1])
            begins = begins[:-1]
        runs.extend(zip(begins.tolist(), ends.tolist()))
        offset += len(frames)
    if open_begin is not None:
        runs.append((open_begin, offset))
    return [(begin, end) for begin, end in runs if end - begin >= min_frames]


def _span(item: Mapping[str, Any]) -> tuple[float, float] | None:
    start, end = item.get("start"), item.get("end")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return None
    return float(start), max(float(start), float(end))


def _unheard(item: Mapping[str, Any], runs: Sequence[Run], starts: list[float]) -> bool:
    """Whether ``item`` lies in one run with UNHEARD_WORD_GUARD_S to spare each side."""
    span = _span(item)
    if span is None:
        return False
    start, end = span
    guard = UNHEARD_WORD_GUARD_S
    index = bisect_right(starts, start - guard + WORD_TIME_TOLERANCE_S) - 1
    if index < 0:
        return False
    run_start, run_end = runs[index]
    return not falls_short(start, run_start + guard) and not exceeds(
        end, run_end - guard
    )


def _without_words(
    segment: dict[str, Any], kept: list[dict[str, Any]]
) -> dict[str, Any]:
    """``segment`` holding only ``kept`` of its words, its text and edges to match."""
    words = segment["words"]
    trimmed = {**segment, "words": kept, "text": reconstruct_text_from_words(kept)}
    if kept[0] is not words[0]:
        trimmed["start"] = kept[0]["start"]
    if kept[-1] is not words[-1]:
        trimmed["end"] = kept[-1]["end"]
    return trimmed


def remove_unheard_words(
    result: Mapping[str, Any], runs: Sequence[Run]
) -> tuple[dict[str, Any], UnheardWords]:
    """``result`` (the engines' canonical schema) less what lies in ``runs``.

    A word goes when it lies in a run with UNHEARD_WORD_GUARD_S to spare on each
    side, and a segment goes when it loses every word. A segment without word
    times is judged on its own span, as one word. A word straddling a run's
    edge, or nearer an edge than the guard, is kept, as is one without usable
    times. ``result`` is not changed: what comes back is a copy whose text is
    rebuilt only when something went.
    """
    starts = [run[0] for run in runs]
    kept_segments: list[dict[str, Any]] = []
    dropped_words = dropped_segments = 0
    for segment in result.get("segments") or []:
        words = segment.get("words") or []
        kept = [word for word in words if not _unheard(word, runs, starts)]
        dropped_words += len(words) - len(kept)
        if (words and not kept) or (not words and _unheard(segment, runs, starts)):
            dropped_segments += 1
        elif len(kept) < len(words):
            kept_segments.append(_without_words(segment, kept))
        else:
            kept_segments.append(segment)
    filtered = dict(result)
    if dropped_words or dropped_segments:
        filtered["segments"] = kept_segments
        texts = (str(segment.get("text") or "").strip() for segment in kept_segments)
        filtered["text"] = " ".join(text for text in texts if text)
    return filtered, UnheardWords(dropped_words, dropped_segments)
