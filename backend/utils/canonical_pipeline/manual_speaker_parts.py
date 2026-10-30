"""Which final words take the speaker a person chose for a line.

A speaker-only correction says who spoke the corrected line's words. The final
pass segments the audio afresh, and can join those words with other speech
into one longer segment, as when a live line picked for one speaker sits
inside another person's long final turn. The choice then goes only to the
words the line covers.

A segment with timed words is split at its word timings where the choice
differs from the speaker the final pass gave it. A word belongs to the
corrected line holding its midpoint, and an untimed word goes with its timed
neighbour, as in preservation. Runs of words outside every corrected line keep
the final speaker. A corrected line whose speaker the segment already has
leaves it whole, so a correction the final pass agrees with does not break up
the line, and the lock covers that segment as before.

A segment with no timed word cannot be split. It takes the speaker of the
corrected lines that cover most of it only when those lines cover more than
``UNTIMED_MIN_SHARE`` of its duration, or when the final pass already gave it
that speaker.

A corrected line whose bounds a final segment matches exactly is the
finaliser's to match: it keeps its own row, speaker and lock there, and carries
onto nothing else. Nothing here changes the final diarisation.

A choice made in an earlier run names one of the recording's speakers, and a
settled reprocess keeps both the line and that speaker's row. Speaker
assignment reuses rows by diarisation label, so after a relabel the row can
hold another voice, under the name that followed that voice. The choice was the
person, so first it follows its speaker's own speech, as names do
(``follow_chosen_speakers``).

A line moved by hand to a live speaker has reached a processed speaker by now
whenever processing found any (``_move_live_lines``). One still holding the
raw live label found none, as when diarisation is off or fails. It carries
nothing, and where a final segment matches it exactly it loses its lock and
takes the final pass's speaker, as every other line of that meeting does, so
no processed line keeps a live label.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import groupby
from typing import Any

from backend.models.pipeline import TranscriptUtterance
from backend.models.speaker import RecordingSpeaker
from backend.utils.live_speaker_labels import live_speaker_display_name
from backend.utils.speaker_names.anchors import Span, lines_from_final_segments
from backend.utils.speaker_names.resolve import NameEvent, place_event
from backend.utils.speaker_names.sources import AUTHORITY, KIND_NAME, SOURCE_USER
from backend.utils.word_text import reconstruct_text_from_words
from backend.utils.word_times import has_word_timing

from .constants import UNKNOWN_SPEAKER, _range_overlap_ms, _segment_to_ms
from .preservation import _middle_ms, timed_word_owners

# A segment with no word timings goes to one speaker's choice only when the
# lines choosing that speaker cover a strict majority of the segment. Below
# that, most of the segment is speech nobody corrected, so it keeps the final
# speaker rather than take a choice made about less than half of it.
UNTIMED_MIN_SHARE = 0.5

Bounds = tuple[int, int]
SpeakerKey = int | str


def carries_manual_speaker(line: TranscriptUtterance) -> bool:
    """A line whose speaker a person chose, as one of the recording's speakers."""
    return bool(line.manual_speaker_locked) and line.recording_speaker_id is not None


def unplaced_live_pick(line: TranscriptUtterance) -> bool:
    """A line moved by hand to a live speaker that processing placed nowhere."""
    return (
        bool(line.manual_speaker_locked)
        and line.recording_speaker_id is None
        and live_speaker_display_name(line.speaker_label) is not None
    )


def final_speaker_value(segment: dict[str, Any]) -> str:
    """The segment's speaker as the final pass gives it, never a live label.

    Only a line preservation retains carries its live label into a segment.
    """
    speaker = str(segment.get("speaker") or UNKNOWN_SPEAKER)
    return UNKNOWN_SPEAKER if live_speaker_display_name(speaker) else speaker


def manual_speaker_key(line: TranscriptUtterance) -> SpeakerKey:
    """The chosen speaker, as the finaliser groups inherited speakers."""
    return line.recording_speaker_id or str(line.speaker_label)


def _chosen_labels(
    lines: Iterable[TranscriptUtterance], speaker_labels: Mapping[int, str]
) -> dict[int | None, str]:
    """Each line's chosen speaker as a label, to compare with a segment's.

    A recording speaker's current label comes first, since the line's own copy
    can predate a later relabel; a live label is its own.
    """
    return {
        line.id: speaker_labels.get(line.recording_speaker_id or 0)
        or str(line.speaker_label or "")
        for line in lines
    }


def _disjoint(spans: Iterable[Span]) -> tuple[Span, ...]:
    merged: list[Span] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _surviving(
    speaker: RecordingSpeaker, by_id: Mapping[int, RecordingSpeaker]
) -> RecordingSpeaker:
    seen = {speaker.id}
    while speaker.merged_into_id and speaker.merged_into_id not in seen:
        target = by_id.get(speaker.merged_into_id)
        if target is None:
            break
        seen.add(target.id)
        speaker = target
    return speaker


def follow_chosen_speakers(
    lines: Sequence[TranscriptUtterance],
    final_segments: Sequence[dict[str, Any]],
    speakers: Sequence[RecordingSpeaker],
) -> None:
    """Move each chosen speaker's lines to the speaker now holding its speech.

    The chosen speaker's own lines from the earlier run, less any moved by
    hand, are placed on this run's final speakers as a user's name is
    (``place_event``), and the choice moves to the surviving speaker they land
    on, as ``_move_live_lines`` moves a live pick. A chosen speaker with no
    such lines (one the correction itself made, a live pick placed this run,
    or one whose every line was moved by hand), or a run with no final
    speakers, keeps its choice as it is. ``final_segments`` are this run's,
    before preservation adds lines it keeps.
    """
    chosen = {
        line.recording_speaker_id
        for line in lines
        if carries_manual_speaker(line) and line.recording_speaker_id is not None
    }
    final_lines = lines_from_final_segments(final_segments)
    if not chosen or not final_lines:
        return
    speech: dict[int, list[Span]] = defaultdict(list)
    for line in lines:
        speaker_id = line.recording_speaker_id
        if speaker_id in chosen and not line.manual_speaker_locked:
            speech[speaker_id].append((line.start_ms, line.end_ms))
    by_id = {int(speaker.id): speaker for speaker in speakers if speaker.id}
    by_label = {
        speaker.diarization_label: speaker
        for speaker in speakers
        if speaker.diarization_label
    }
    moved: dict[int, RecordingSpeaker] = {}
    for speaker_id, spans in speech.items():
        disjoint = _disjoint(spans)
        label = place_event(
            NameEvent(
                event_id=0,
                kind=KIND_NAME,
                name="",
                global_speaker_id=None,
                source=SOURCE_USER,
                authority=AUTHORITY[SOURCE_USER],
                anchor_start_ms=disjoint[0][0],
                anchor_end_ms=disjoint[-1][1],
                anchor_spans=disjoint,
            ),
            final_lines,
        ).label
        holder = by_label.get(label or "")
        if holder is not None:
            moved[speaker_id] = _surviving(holder, by_id)
    for line in lines:
        target = moved.get(line.recording_speaker_id or 0)
        if carries_manual_speaker(line) and target is not None:
            line.recording_speaker_id = target.id
            line.speaker_label = target.diarization_label


def _segment_bounds(segment: dict[str, Any]) -> Bounds:
    return _segment_to_ms(segment.get("start", 0.0)), _segment_to_ms(
        segment.get("end", 0.0)
    )


def _exact_matches(
    lines: Sequence[TranscriptUtterance], bounds: Sequence[Bounds]
) -> tuple[set[int], set[int]]:
    """The lines and segment indexes the finaliser pairs by identical bounds.

    The finaliser takes, for each segment in order, the first line not yet
    taken with the same start and end, so this pairs them the same way.
    """
    waiting: dict[Bounds, list[int]] = defaultdict(list)
    for line in lines:
        if line.id is not None:
            waiting[(line.start_ms, line.end_ms)].append(line.id)
    matched_lines: set[int] = set()
    matched_segments: set[int] = set()
    for index, key in enumerate(bounds):
        if waiting.get(key):
            matched_lines.add(waiting[key].pop(0))
            matched_segments.add(index)
    return matched_lines, matched_segments


def _covered_ms(span: Bounds, lines: Iterable[TranscriptUtterance]) -> int:
    """Milliseconds of ``span`` inside at least one of ``lines``."""
    clipped = sorted(
        (max(span[0], line.start_ms), min(span[1], line.end_ms)) for line in lines
    )
    covered, reach = 0, span[0]
    for start, end in clipped:
        start = max(start, reach)
        if end > start:
            covered += end - start
            reach = end
    return covered


def _untimed_sources(
    segment: dict[str, Any],
    span: Bounds,
    found: Sequence[TranscriptUtterance],
    labels: Mapping[int | None, str],
) -> frozenset[int]:
    by_key: dict[SpeakerKey, list[TranscriptUtterance]] = defaultdict(list)
    for line in found:
        by_key[manual_speaker_key(line)].append(line)
    ranked = sorted(
        ((_covered_ms(span, lines), key) for key, lines in by_key.items()),
        key=lambda item: (-item[0], str(item[1])),
    )
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return frozenset()
    covered, key = ranked[0]
    lines = by_key[key]
    already_theirs = labels[lines[0].id] == str(segment.get("speaker") or "")
    if already_theirs or covered > (span[1] - span[0]) * UNTIMED_MIN_SHARE:
        return frozenset(line.id for line in lines if line.id is not None)
    return frozenset()


def _timed_parts(
    segment: dict[str, Any],
    found: Sequence[TranscriptUtterance],
    labels: Mapping[int | None, str],
) -> list[tuple[dict[str, Any], frozenset[int]]]:
    """The segment's runs of words, each with the corrected lines it inherits from."""
    words: list[dict[str, Any]] = segment["words"]
    own_label = str(segment.get("speaker") or "")

    def holder(word: dict[str, Any]) -> TranscriptUtterance | None:
        middle = _middle_ms(word)
        return next(
            (line for line in found if line.start_ms <= middle < line.end_ms), None
        )

    def run_key(owner: TranscriptUtterance | None) -> SpeakerKey | None:
        # Words the final pass already gives the chosen speaker stay in the
        # segment's own run, so agreeing with a correction never splits it.
        if owner is None or labels[owner.id] == own_label:
            return None
        return manual_speaker_key(owner)

    owners = timed_word_owners(words, holder)
    runs: list[tuple[list[dict[str, Any]], frozenset[int]]] = []
    for _key, run in groupby(zip(words, owners), key=lambda pair: run_key(pair[1])):
        pairs = list(run)
        runs.append(
            (
                [word for word, _owner in pairs],
                frozenset(
                    owner.id
                    for _word, owner in pairs
                    if owner is not None and owner.id is not None
                ),
            )
        )
    if len(runs) == 1:
        return [(dict(segment), runs[0][1])]

    # Parts tile the segment without overlapping, so the cut between two runs
    # falls where the later run's first timed word starts. The outer edges are
    # the segment's own.
    cuts = [float(segment.get("start", 0.0))]
    for kept, _sources in runs[1:]:
        first = next(float(word["start"]) for word in kept if has_word_timing(word))
        cuts.append(max(first, cuts[-1]))
    cuts.append(max(float(segment.get("end", 0.0)), cuts[-1]))
    return [
        (
            {
                **segment,
                "words": kept,
                "text": reconstruct_text_from_words(kept),
                "start": cuts[index],
                "end": cuts[index + 1],
            },
            sources,
        )
        for index, (kept, sources) in enumerate(runs)
    ]


def split_for_manual_speakers(
    lines: Sequence[TranscriptUtterance],
    segments: Sequence[dict[str, Any]],
    speaker_labels: Mapping[int, str] | None = None,
) -> tuple[list[dict[str, Any]], list[frozenset[int]]]:
    """Final segments split where a chosen speaker covers part of them.

    ``speaker_labels`` maps the recording's speaker ids to their labels.
    Returns the segments, in order, and for each one the ids of the corrected
    lines whose speaker it inherits; an empty set keeps the final speaker.
    """
    bounds = [_segment_bounds(segment) for segment in segments]
    matched_lines, matched_segments = _exact_matches(lines, bounds)
    carriers = [
        line
        for line in lines
        if carries_manual_speaker(line) and line.id not in matched_lines
    ]
    labels = _chosen_labels(carriers, speaker_labels or {})
    result: list[dict[str, Any]] = []
    sources: list[frozenset[int]] = []
    for index, (segment, span) in enumerate(zip(segments, bounds)):
        found = [
            line
            for line in carriers
            if _range_overlap_ms(*span, line.start_ms, line.end_ms) > 0
        ]
        parts: list[tuple[dict[str, Any], frozenset[int]]]
        if index in matched_segments or not found:
            parts = [(dict(segment), frozenset())]
        elif any(has_word_timing(word) for word in segment.get("words") or []):
            parts = _timed_parts(segment, found, labels)
        else:
            parts = [(dict(segment), _untimed_sources(segment, span, found, labels))]
        for part, part_sources in parts:
            result.append(part)
            sources.append(part_sources)
    return result, sources


__all__ = [
    "UNTIMED_MIN_SHARE",
    "carries_manual_speaker",
    "final_speaker_value",
    "follow_chosen_speakers",
    "manual_speaker_key",
    "split_for_manual_speakers",
    "unplaced_live_pick",
]
