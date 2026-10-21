# Backstop for speech an onnx-asr window's decoder skipped: which stretches of
# a decoded window hold kept audio but no word, and so are decoded again.
# numpy is imported inside the functions, like the engine's other heavy imports.

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    from .onnx_asr_engine import Recognition

# Least kept (non-zero) audio, in seconds, between one recognised word and the
# next that counts as speech the decoder skipped, and is decoded again. It is
# the minimum the backstop was measured with on the benchmark recordings;
# shorter stretches are left as the window's call decoded them.
HOLE_MIN_SPEECH_S = 3.0

# Shortest run, in seconds, of exact-zero samples taken as a VAD silence.
MIN_SILENT_RUN_S = 0.2

# Token timestamps mark where a token starts, not where its sound ends, so a
# recognised word's tail runs on past its last subtoken. The search for the VAD
# silence that ends it starts this many seconds after that subtoken.
SILENCE_SEARCH_DELAY_S = 0.2

# When no VAD silence starts within this many seconds of a word's last
# subtoken, the stretch after the word starts this far after it, so the word's
# tail is not decoded a second time.
WORD_TAIL_GUARD_S = 0.8

# Both are the values the backstop was measured with. They are measured from
# the last subtoken, not from the word ends the mapper gives (which are capped
# by the word length setting), and no setting changes them.

# Parakeet stamps a word on an 80 ms frame, often after its sound starts, so
# the stretch before a recognised word ends at the last VAD silence that ends
# within WORD_TAIL_GUARD_S of the word's first subtoken, mirroring the rule
# after a word. Failing that it ends this many seconds before that subtoken:
# two frames, past 95% of the onsets measured after a silence on the
# benchmark recordings.
WORD_ONSET_GUARD_S = 0.16

# A hole is retried at most twice. The first retry starts on its first kept
# sample. A hole that retry leaves wordless is decoded once more from
# second_try_start(), the last subtoken of the word before it, so the call
# enters the hole just after speech it has decoded; only the words it finds
# from the hole's first kept sample on are kept. A hole with no word before it
# in its window gets no second try. What the second try leaves wordless is only
# counted: nothing is retried a third time, and a second try is never retried.


class WordTiming(NamedTuple):
    """When a recognised word's first and last subtokens start, in seconds."""

    start: float
    last_token: float


def word_timings(recognized: Recognition) -> tuple[WordTiming, ...]:
    """Each word's first and last subtoken times, grouped as the mapper groups them.

    A word begins at the first token and at every token that starts with a
    space. ``recognized`` is empty or has a timestamp for every token: a
    result the mapper cannot time is never searched for holes.
    """
    tokens, timestamps = recognized.tokens, recognized.timestamps
    if not tokens or not timestamps:
        return ()
    timings: list[WordTiming] = []
    for index, token in enumerate(tokens):
        stamp = timestamps[index]
        if index == 0 or token.startswith(" "):
            timings.append(WordTiming(stamp, stamp))
        else:
            timings[-1] = WordTiming(timings[-1].start, stamp)
    return tuple(timings)


def _silent_runs(audio: NDArray[np.float32], sample_rate: int) -> NDArray[np.intp]:
    """(begin, end) frames of every exact-zero run of MIN_SILENT_RUN_S or more."""
    import numpy as np

    silent = np.concatenate(([False], audio == 0, [False]))
    runs = np.flatnonzero(np.diff(silent.astype(np.int8))).reshape(-1, 2)
    return runs[(runs[:, 1] - runs[:, 0]) >= int(MIN_SILENT_RUN_S * sample_rate)]


def _after_word(
    audio: NDArray[np.float32],
    sample_rate: int,
    last_token: float,
    high: int,
) -> int:
    """The first frame after a recognised word that may hold skipped speech.

    That is the end of the first VAD silence found from SILENCE_SEARCH_DELAY_S
    after the word's last subtoken that starts within WORD_TAIL_GUARD_S of it,
    else the end of that guard. Never past ``high``.
    """
    low = max(0, min(high, round((last_token + SILENCE_SEARCH_DELAY_S) * sample_rate)))
    guard = min(high, round((last_token + WORD_TAIL_GUARD_S) * sample_rate))
    runs = _silent_runs(audio[low:high], sample_rate)
    if len(runs) and low + int(runs[0, 0]) <= guard:
        return low + int(runs[0, 1])
    return guard


def _before_word(
    audio: NDArray[np.float32],
    sample_rate: int,
    start: float,
    low: int,
) -> int:
    """The frame after the last that may hold skipped speech before a word.

    That is the start of the last VAD silence before the word's first
    subtoken that ends within WORD_TAIL_GUARD_S of it (or after it), else
    WORD_ONSET_GUARD_S before that subtoken. Never before ``low``.
    """
    stamp = max(low, min(len(audio), round(start * sample_rate)))
    search = round((start - WORD_TAIL_GUARD_S) * sample_rate)
    guard = max(low, round((start - WORD_ONSET_GUARD_S) * sample_rate))
    # A silence that starts before the stamp is measured to MIN_SILENT_RUN_S
    # past it, so one the stamp falls inside still counts.
    reach = min(len(audio), stamp + round(MIN_SILENT_RUN_S * sample_rate))
    runs = _silent_runs(audio[low:reach], sample_rate)
    runs = runs[low + runs[:, 0] < stamp]
    if len(runs) and low + int(runs[-1, 1]) >= search:
        return low + int(runs[-1, 0])
    return min(stamp, guard)


def _uncovered_spans(
    audio: NDArray[np.float32],
    sample_rate: int,
    words: Sequence[WordTiming],
) -> list[tuple[int, int]]:
    """Frames no recognised word covers: before the first word, after each one.

    ``words`` are in time order, as the decoder emits them. A window with no
    word is uncovered whole.
    """
    if not words:
        return [(0, len(audio))]
    spans = [(0, _before_word(audio, sample_rate, words[0].start, 0))]
    last_token = words[0].start
    for index, word in enumerate(words):
        high = (
            _before_word(
                audio,
                sample_rate,
                words[index + 1].start,
                max(0, min(len(audio), round(word.start * sample_rate))),
            )
            if index + 1 < len(words)
            else len(audio)
        )
        last_token = max(last_token, word.start, word.last_token)
        low = _after_word(audio, sample_rate, last_token, high)
        spans.append((low, high))
    return spans


class Survey(NamedTuple):
    """What a window's stretches without a word hold (frames)."""

    # Sorted, disjoint (begin, end) frames of each hole: decoded again.
    holes: list[tuple[int, int]]
    # Kept frames of each stretch with some kept audio but under
    # HOLE_MIN_SPEECH_S of it: left as the window's call decoded it.
    short_kept_frames: list[int]


def survey(
    audio: NDArray[np.float32],
    sample_rate: int,
    words: Sequence[WordTiming],
) -> Survey:
    """A window's holes, and the shorter wordless stretches it leaves.

    ``audio`` must be VAD-muted: exact zeros are taken for the VAD's silences
    and every other sample for kept speech. On unmuted audio every gap between
    words would look like skipped speech, so callers pass muted audio only.

    A hole is what lies between recognised words (``words``, from the window's
    own decode, in time order), less a guard after each word's last subtoken
    and before its first, trimmed to its first and last non-zero samples. It
    counts only if it holds at least HOLE_MIN_SPEECH_S of non-zero audio.
    Holes are not split here: the caller decides how long a call decoding one
    again may be.
    """
    import numpy as np

    min_speech = int(HOLE_MIN_SPEECH_S * sample_rate)
    holes: list[tuple[int, int]] = []
    short: list[int] = []
    for low, high in _uncovered_spans(audio, sample_rate, words):
        speech = np.flatnonzero(audio[low:high])
        if len(speech) < min_speech:
            if len(speech):
                short.append(len(speech))
            continue
        holes.append((low + int(speech[0]), low + int(speech[-1]) + 1))
    return Survey(holes, short)


def second_try_start(
    words: Sequence[WordTiming], begin: int, sample_rate: int
) -> int | None:
    """The frame a wordless hole's second try starts on, or None for no try.

    That is the latest last subtoken of the words that start before the hole
    (``begin``), never past ``begin``. ``words`` are the window's own and its
    first retry's. None when no word starts before the hole.
    """
    earlier = [
        word.last_token for word in words if round(word.start * sample_rate) < begin
    ]
    if not earlier:
        return None
    return max(0, min(begin, round(max(earlier) * sample_rate)))


class SecondTry(NamedTuple):
    """What a window's second tries did with the holes its retry left wordless."""

    # Holes decoded once more from the word before them.
    tried: list[tuple[int, int]]
    calls: int
    # How many of those it found words in, and how many words.
    found_in: int
    words: int
    # Holes with no word before them in the window: not tried again.
    no_previous_word: list[tuple[int, int]]


def _kept_frames(audio: NDArray[np.float32], holes: Sequence[tuple[int, int]]) -> int:
    import numpy as np

    return sum(int(np.count_nonzero(audio[begin:end])) for begin, end in holes)


def _overlaps(
    holes: Sequence[tuple[int, int]], others: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    """The frames that lie in both a hole of ``holes`` and one of ``others``."""
    spans = ((max(b, ob), min(e, oe)) for b, e in holes for ob, oe in others)
    return [(low, high) for low, high in spans if low < high]


@dataclass
class Tally:
    """One recording's backstop counts: what it retried and what it left.

    Holes retried once are counted in frames from their first to their last
    kept sample; everything else in kept frames. Nothing here changes what is
    decoded.
    """

    retried: int = 0
    retried_frames: int = 0
    calls: int = 0
    # Holes the first retry left wordless and that were tried once more.
    retried_again: int = 0
    retried_again_kept_frames: int = 0
    calls_again: int = 0
    # How many of those the second try found words in, how many words, and
    # how much of their kept audio is in no wordless hole after it.
    recovered: int = 0
    recovered_words: int = 0
    recovered_kept_frames: int = 0
    # Holes the first retry left wordless with no word before them.
    no_previous_word: int = 0
    no_previous_word_kept_frames: int = 0
    # Holes that still hold no word once every retry's words are merged in.
    wordless: int = 0
    wordless_kept_frames: int = 0
    # Stretches under HOLE_MIN_SPEECH_S of kept audio, never retried.
    short: int = 0
    short_kept_frames: int = 0

    def add_survey(self, found: Survey) -> None:
        """Count a window's stretches too short to retry."""
        self.short += len(found.short_kept_frames)
        self.short_kept_frames += sum(found.short_kept_frames)

    def add_retry(
        self,
        audio: NDArray[np.float32],
        holes: Sequence[tuple[int, int]],
        calls: int,
        second: SecondTry,
        wordless: Sequence[tuple[int, int]],
    ) -> None:
        """Count a window's retried holes, its second tries, and what is left."""
        tried_kept = _kept_frames(audio, second.tried)
        self.retried += len(holes)
        self.retried_frames += sum(end - begin for begin, end in holes)
        self.calls += calls
        self.retried_again += len(second.tried)
        self.retried_again_kept_frames += tried_kept
        self.calls_again += second.calls
        self.recovered += second.found_in
        self.recovered_words += second.words
        self.recovered_kept_frames += tried_kept - _kept_frames(
            audio, _overlaps(second.tried, wordless)
        )
        self.no_previous_word += len(second.no_previous_word)
        self.no_previous_word_kept_frames += _kept_frames(
            audio, second.no_previous_word
        )
        self.wordless += len(wordless)
        self.wordless_kept_frames += _kept_frames(audio, wordless)

    def summary(self, sample_rate: int) -> str:
        """The counts as one log line, in seconds."""

        def seconds(frames: int) -> str:
            return f"{frames / sample_rate:.1f} s"

        return (
            f"backstop: {self.retried} holes retried "
            f"({seconds(self.retried_frames)}, {self.calls} calls); "
            f"{self.retried_again} still wordless retried again from the word "
            f"before them ({seconds(self.retried_again_kept_frames)} kept, "
            f"{self.calls_again} calls), finding {self.recovered_words} words "
            f"in {self.recovered} ({seconds(self.recovered_kept_frames)} kept "
            f"no longer wordless); {self.no_previous_word} still wordless with "
            f"no word before them not retried again "
            f"({seconds(self.no_previous_word_kept_frames)} kept); "
            f"{self.wordless} holes ({seconds(self.wordless_kept_frames)} kept) "
            f"still wordless; {self.short} holes under {HOLE_MIN_SPEECH_S:g} s "
            f"skipped ({seconds(self.short_kept_frames)} kept)"
        )
