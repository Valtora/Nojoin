"""Deterministic meeting metrics derived from canonical utterances.

Pure functions over plain rows: no ORM, no session, no heavy imports. This
module runs inside the API request path, so it must stay import-light, and it
is the reason the analytics tier needs no persistence at all -- a speaker
merge or a text edit is reflected on the next read because nothing was stored.

Two conventions hold throughout:

* Per-speaker talk time counts overlapping speech once for each speaker, so
  the per-speaker figures can sum to more than the wall-clock duration. Shares
  are therefore reported against total *speech*, not against duration, and the
  duration-relative figure is carried separately where it is wanted.
* Anything excluded from a statistic is counted, never silently dropped. A
  median over four samples out of ninety is not the same claim as a median over
  ninety, and the caller has to be able to tell.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable, Sequence

from .constants import (
    LATENCY_FLOOR_MS,
    MIN_UTTERANCE_MS_FOR_TURN_STATS,
    OVERLAP_FLOOR_MS,
    TIMELINE_MIN_BUCKET_MS,
    TIMELINE_TARGET_BUCKETS,
    TURN_GAP_MS,
)


@dataclass(frozen=True)
class UtteranceRow:
    """One active utterance, reduced to what the metrics actually need."""

    speaker_key: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        # Defensive rather than theoretical: a legacy row imported from the
        # segment projection can carry an end at or before its start, and a
        # negative duration would silently subtract from a speaker's total.
        return max(0, self.end_ms - self.start_ms)


@dataclass(frozen=True)
class Turn:
    speaker_key: str
    start_ms: int
    end_ms: int
    utterance_count: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


def sort_utterances(utterances: Iterable[UtteranceRow]) -> list[UtteranceRow]:
    """Time order, with end time breaking ties so sweeps stay deterministic."""
    return sorted(utterances, key=lambda u: (u.start_ms, u.end_ms, u.speaker_key))


def merge_intervals(utterances: Sequence[UtteranceRow]) -> list[tuple[int, int]]:
    """Collapse overlapping utterances into non-overlapping speech intervals."""
    merged: list[tuple[int, int]] = []
    for utterance in sort_utterances(utterances):
        if utterance.duration_ms <= 0:
            continue
        if merged and utterance.start_ms <= merged[-1][1]:
            start, end = merged[-1]
            merged[-1] = (start, max(end, utterance.end_ms))
        else:
            merged.append((utterance.start_ms, utterance.end_ms))
    return merged


def compute_talk_time(
    utterances: Sequence[UtteranceRow], duration_ms: int
) -> dict[str, dict[str, float]]:
    """Per-speaker speech totals, plus both share denominators."""
    totals: dict[str, int] = {}
    for utterance in utterances:
        totals[utterance.speaker_key] = (
            totals.get(utterance.speaker_key, 0) + utterance.duration_ms
        )

    total_speech_ms = sum(totals.values())
    return {
        speaker_key: {
            "speech_ms": speech_ms,
            "share_of_speech": (
                round(speech_ms / total_speech_ms, 4) if total_speech_ms else 0.0
            ),
            "share_of_duration": (
                round(speech_ms / duration_ms, 4) if duration_ms > 0 else 0.0
            ),
        }
        for speaker_key, speech_ms in totals.items()
    }


def build_turns(utterances: Sequence[UtteranceRow]) -> list[Turn]:
    """Group time-ordered utterances into turns.

    A turn breaks when the speaker changes or when the gap since the running
    turn's end reaches ``TURN_GAP_MS``. Interleaving caused by overlapping
    speech therefore breaks a turn, which is correct: if someone else spoke in
    between, the first speaker did not hold the floor continuously.
    """
    turns: list[Turn] = []
    for utterance in sort_utterances(utterances):
        if utterance.duration_ms <= 0:
            continue
        current = turns[-1] if turns else None
        continues = (
            current is not None
            and current.speaker_key == utterance.speaker_key
            and utterance.start_ms - current.end_ms < TURN_GAP_MS
        )
        if current is not None and continues:
            turns[-1] = Turn(
                speaker_key=current.speaker_key,
                start_ms=current.start_ms,
                end_ms=max(current.end_ms, utterance.end_ms),
                utterance_count=current.utterance_count + 1,
            )
        else:
            turns.append(
                Turn(
                    speaker_key=utterance.speaker_key,
                    start_ms=utterance.start_ms,
                    end_ms=utterance.end_ms,
                    utterance_count=1,
                )
            )
    return turns


def compute_turn_structure(turns: Sequence[Turn]) -> dict[str, dict[str, object]]:
    """Turn count, median and longest turn per speaker."""
    by_speaker: dict[str, list[Turn]] = {}
    for turn in turns:
        by_speaker.setdefault(turn.speaker_key, []).append(turn)

    structure: dict[str, dict[str, object]] = {}
    for speaker_key, speaker_turns in by_speaker.items():
        measurable = [
            turn
            for turn in speaker_turns
            if turn.duration_ms >= MIN_UTTERANCE_MS_FOR_TURN_STATS
        ]
        longest = max(speaker_turns, key=lambda turn: turn.duration_ms)
        structure[speaker_key] = {
            "turn_count": len(speaker_turns),
            "median_turn_ms": (
                int(median(turn.duration_ms for turn in measurable))
                if measurable
                else 0
            ),
            "longest_turn_ms": longest.duration_ms,
            # Carried so the interface can seek to the monologue it names.
            # A number nobody can check is a number nobody trusts.
            "longest_turn_start_ms": longest.start_ms,
            "excluded_short_turns": len(speaker_turns) - len(measurable),
        }
    return structure


def compute_silence(utterances: Sequence[UtteranceRow], duration_ms: int) -> dict:
    """Speech and dead-air totals against the recording's wall clock."""
    speech_ms = sum(end - start for start, end in merge_intervals(utterances))
    if duration_ms <= 0:
        return {
            "speech_ms": speech_ms,
            "silence_ms": 0,
            "silence_share": 0.0,
        }
    silence_ms = max(0, duration_ms - speech_ms)
    return {
        "speech_ms": speech_ms,
        "silence_ms": silence_ms,
        "silence_share": round(silence_ms / duration_ms, 4),
    }


def compute_overlap(utterances: Sequence[UtteranceRow]) -> dict:
    """Total overlapped speech, as an absolute figure and a share."""
    ordered = sort_utterances(utterances)
    total_speech_ms = sum(utterance.duration_ms for utterance in ordered)
    merged_ms = sum(end - start for start, end in merge_intervals(ordered))
    overlapped_ms = max(0, total_speech_ms - merged_ms)
    return {
        "overlapped_ms": overlapped_ms,
        "overlap_share": (
            round(overlapped_ms / total_speech_ms, 4) if total_speech_ms else 0.0
        ),
    }


def compute_interruptions(
    utterances: Sequence[UtteranceRow],
) -> dict[str, dict[str, int]]:
    """Directional interruption counts.

    Speaker B interrupts speaker A when B starts while A is still speaking and
    A had started first, with ``OVERLAP_FLOOR_MS`` of A's utterance still to
    run. Direction is the whole point: being interrupted and interrupting are
    different behaviours, and a symmetric overlap count conflates them.
    """
    counts: dict[str, dict[str, int]] = {}

    def bucket(speaker_key: str) -> dict[str, int]:
        return counts.setdefault(speaker_key, {"made": 0, "received": 0})

    ordered = sort_utterances(utterances)
    open_utterances: list[UtteranceRow] = []
    for utterance in ordered:
        open_utterances = [
            candidate
            for candidate in open_utterances
            if candidate.end_ms > utterance.start_ms
        ]
        interrupted: set[str] = set()
        for candidate in open_utterances:
            if candidate.speaker_key == utterance.speaker_key:
                continue
            # Strictly earlier, not merely sorted earlier. Two speakers
            # starting in the same millisecond interrupted nobody, and the
            # sort's tiebreak would otherwise pick a direction arbitrarily.
            if candidate.start_ms >= utterance.start_ms:
                continue
            if utterance.start_ms < candidate.end_ms - OVERLAP_FLOOR_MS:
                interrupted.add(candidate.speaker_key)
        for speaker_key in interrupted:
            bucket(speaker_key)["received"] += 1
        if interrupted:
            bucket(utterance.speaker_key)["made"] += 1
        else:
            bucket(utterance.speaker_key)
        open_utterances.append(utterance)

    return counts


def compute_transitions(turns: Sequence[Turn]) -> dict:
    """Who follows whom, and how long they leave before answering.

    Latency samples below ``LATENCY_FLOOR_MS`` (including the negatives that
    overlapping speech produces) are excluded and counted, not clamped: a gap
    of -400ms is an interruption, already reported as one, and folding it into
    a response-time median would describe a conversation that did not happen.
    """
    pair_counts: dict[tuple[str, str], int] = {}
    pair_latencies: dict[tuple[str, str], list[int]] = {}
    speaker_latencies: dict[str, list[int]] = {}
    excluded = 0

    for previous, current in zip(turns, turns[1:]):
        if previous.speaker_key == current.speaker_key:
            continue
        pair = (previous.speaker_key, current.speaker_key)
        pair_counts[pair] = pair_counts.get(pair, 0) + 1
        gap_ms = current.start_ms - previous.end_ms
        if gap_ms < LATENCY_FLOOR_MS:
            excluded += 1
            continue
        pair_latencies.setdefault(pair, []).append(gap_ms)
        speaker_latencies.setdefault(current.speaker_key, []).append(gap_ms)

    return {
        "transitions": [
            {
                "from_speaker": from_key,
                "to_speaker": to_key,
                "count": count,
                "median_latency_ms": (
                    int(median(pair_latencies[(from_key, to_key)]))
                    if pair_latencies.get((from_key, to_key))
                    else None
                ),
            }
            for (from_key, to_key), count in sorted(
                pair_counts.items(), key=lambda item: item[1], reverse=True
            )
        ],
        "response_latency": {
            speaker_key: {
                "median_ms": int(median(samples)),
                "sample_count": len(samples),
            }
            for speaker_key, samples in speaker_latencies.items()
        },
        "excluded_latency_samples": excluded,
    }


def _timeline_bucket_ms(duration_ms: int) -> int:
    if duration_ms <= 0:
        return TIMELINE_MIN_BUCKET_MS
    derived = -(-duration_ms // TIMELINE_TARGET_BUCKETS)  # ceiling division
    return max(TIMELINE_MIN_BUCKET_MS, derived)


def compute_timeline(utterances: Sequence[UtteranceRow], duration_ms: int) -> dict:
    """Talk share per speaker across fixed-width buckets.

    An utterance spanning a boundary is split proportionally rather than
    assigned to the bucket it starts in, so a four-minute monologue does not
    appear as a spike in one bucket and silence in the next.
    """
    bucket_ms = _timeline_bucket_ms(duration_ms)
    span_ms = duration_ms if duration_ms > 0 else _max_end_ms(utterances)
    bucket_count = max(1, -(-span_ms // bucket_ms)) if span_ms > 0 else 1

    buckets: list[dict[str, int]] = [{} for _ in range(bucket_count)]
    for utterance in utterances:
        if utterance.duration_ms <= 0:
            continue
        first = min(utterance.start_ms // bucket_ms, bucket_count - 1)
        last = min((utterance.end_ms - 1) // bucket_ms, bucket_count - 1)
        for index in range(first, last + 1):
            bucket_start = index * bucket_ms
            bucket_end = bucket_start + bucket_ms
            overlap = min(utterance.end_ms, bucket_end) - max(
                utterance.start_ms, bucket_start
            )
            if overlap > 0:
                speaker_totals = buckets[index]
                speaker_totals[utterance.speaker_key] = (
                    speaker_totals.get(utterance.speaker_key, 0) + overlap
                )

    return {
        "bucket_ms": bucket_ms,
        "buckets": [
            {
                "start_ms": index * bucket_ms,
                "end_ms": min((index + 1) * bucket_ms, span_ms)
                if span_ms
                else bucket_ms,
                "speech_ms": dict(sorted(speaker_totals.items())),
            }
            for index, speaker_totals in enumerate(buckets)
        ],
    }


def _max_end_ms(utterances: Sequence[UtteranceRow]) -> int:
    return max((utterance.end_ms for utterance in utterances), default=0)


def compute_deterministic_metrics(
    utterances: Sequence[UtteranceRow], duration_ms: int
) -> dict:
    """Assemble every deterministic metric for one recording."""
    ordered = sort_utterances(utterances)
    effective_duration_ms = duration_ms if duration_ms > 0 else _max_end_ms(ordered)
    turns = build_turns(ordered)

    return {
        "utterance_count": len(ordered),
        "duration_ms": effective_duration_ms,
        "talk_time": compute_talk_time(ordered, effective_duration_ms),
        "turn_structure": compute_turn_structure(turns),
        "interruptions": compute_interruptions(ordered),
        "turn_taking": compute_transitions(turns),
        "timeline": compute_timeline(ordered, effective_duration_ms),
        "silence": compute_silence(ordered, effective_duration_ms),
        "overlap": compute_overlap(ordered),
    }
