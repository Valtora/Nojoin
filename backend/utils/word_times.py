"""Comparing word times with thresholds, with slack for float error.

Parakeet token times are multiples of onnx-asr's 80 ms frame, and a word ends
at the next word's start, its window's end or its start plus the word length
cap. A difference of word times can therefore equal a threshold exactly: two
starts 1.2 s apart, less a 0.4 s cap, are the 0.8 s segment pause, and a word
9.6 s after a segment's first start, capped at 0.4 s, ends it at the 10 s
segment maximum. Whisper times are hundredths of a second, so two of them
0.01 s apart meet consolidation's contiguous-gap tolerance. Float arithmetic
lands such a difference up to a few 1e-12 s either side of the threshold, which
would leave the comparison to rounding. These comparisons treat a value within
WORD_TIME_TOLERANCE_S of the threshold as equal to it, whichever engine produced
the times.
"""

# Far above the float error of word times, which stays under 1e-10 s at
# offsets up to 100 hours, and far below any duration that means something:
# one sample at 16 kHz lasts 62.5e-6 s.
WORD_TIME_TOLERANCE_S = 1e-6


def exceeds(value_s: float, limit_s: float) -> bool:
    """Whether value_s is more than limit_s by more than float error."""
    return value_s > limit_s + WORD_TIME_TOLERANCE_S


def falls_short(value_s: float, limit_s: float) -> bool:
    """Whether value_s is less than limit_s by more than float error."""
    return value_s < limit_s - WORD_TIME_TOLERANCE_S
