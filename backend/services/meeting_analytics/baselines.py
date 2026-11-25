"""Within-person delivery baselines across a user's other meetings.

The delivery panel compares a speaker with the other people in the same
meeting, which answers "was she the fast one here". This answers the other
defensible question: "was this her usual pace" -- the model-free version of
what emotion models pretend to do, built purely on measured figures the
library already stores.

Derived on read, like the deterministic tier, and for the same reasons: a
speaker merge or rename redirects the person link and the baseline follows on
the next read, with no invalidation path to get wrong. The meetings read are
the ones the person's People entry counts (``counted_speaker``), so a speaker
merged away, one whose lines all moved to someone else, and a meeting in the
bin add nothing. Only figures produced by the current extraction procedure
are compared -- a v1 figure next to a v2 figure resembles a comparison but is
not one, exactly as with voiceprint versions -- and a person needs several
measured meetings before "their usual" means anything at all.
"""

from __future__ import annotations

import json
import logging
from statistics import median
from typing import Any

from sqlmodel import col, select

from backend.models.recording import Recording
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.processing.delivery_descriptors import DELIVERY_METHOD_VERSION
from backend.services.person_meetings import counted_speaker

logger = logging.getLogger(__name__)

# Below this many measured meetings a baseline describes a couple of moments,
# not a habit. Three is a judgement call, disclosed with the figure: it is the
# smallest count where a median is not simply one meeting picked arbitrarily.
BASELINE_MIN_MEETINGS = 3


def _figures_for_speaker(payload: dict, recording_speaker_id: int) -> dict | None:
    if not payload or payload.get("method_version") != DELIVERY_METHOD_VERSION:
        return None
    delivery = payload.get("delivery") or {}
    return (delivery.get("speakers") or {}).get(f"rs:{recording_speaker_id}")


def compute_delivery_baselines(
    session,
    *,
    user_id: int,
    recording_id: int,
    speakers: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """One baseline per speaker key, for speakers linked to a person.

    Reads every completed delivery payload the user's other recordings hold
    for the same person, in the meetings their People entry counts, and
    reduces each metric to a median across meetings, with the meeting count
    carried so the interface can say how much history stands behind the words
    "their usual". A meeting is one sample however many of its speakers are
    linked to the person, and every speaker linked to them in this meeting
    gets the same baseline.
    """
    by_global: dict[int, list[str]] = {}
    for speaker in speakers:
        global_id = speaker.get("global_speaker_id")
        if global_id is not None:
            by_global.setdefault(int(global_id), []).append(speaker["speaker_key"])
    if not by_global:
        return {}

    # The person's speakers in the meetings their People entry counts: owned,
    # not in the bin, and through a speaker counted_speaker() keeps.
    statement = (
        select(
            col(RecordingSpeaker.global_speaker_id),
            col(Recording.id),
            col(RecordingSpeaker.id),
            col(Transcript.analytics_payload),
        )
        .join(Recording, col(Recording.id) == col(RecordingSpeaker.recording_id))
        .join(Transcript, col(Transcript.recording_id) == col(Recording.id))
        .where(col(RecordingSpeaker.global_speaker_id).in_(list(by_global)))
        .where(col(Recording.user_id) == user_id)
        .where(col(Recording.id) != recording_id)
        .where(col(Recording.is_deleted).is_(False))
        .where(counted_speaker())
        .where(col(Transcript.analytics_status) == "completed")
    )
    rows = session.execute(statement).all()

    samples = _collect_samples(rows)

    baselines: dict[str, dict[str, Any]] = {}
    for global_id, bucket in samples.items():
        meetings = max(len(values) for values in bucket.values())
        if meetings < BASELINE_MIN_MEETINGS:
            continue
        for speaker_key in by_global[global_id]:
            baselines[speaker_key] = {
                "meetings": meetings,
                "words_per_minute": _reduce(bucket["wpm"], digits=0),
                "pitch_spread_semitones": _reduce(bucket["pitch_spread"], digits=2),
                "pauses_per_minute": _reduce(bucket["pause_rate"], digits=2),
            }
    return baselines


def _collect_samples(rows) -> dict[int, dict[str, list[float]]]:
    """Per-person metric samples, one value per comparable meeting.

    The People count counts a meeting once however many of its speakers are
    linked to the person, so the rows are grouped by meeting first and each
    group becomes one sample (``_meeting_sample``).
    """
    by_meeting: dict[tuple[int, int], list[dict]] = {}
    for global_id, meeting_id, recording_speaker_id, payload in rows:
        # The select decodes the JSON column on Postgres and SQLite alike, so
        # text here is a payload stored as an encoded JSON string.
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        figures = _figures_for_speaker(payload or {}, recording_speaker_id)
        if not figures:
            continue
        by_meeting.setdefault((int(global_id), int(meeting_id)), []).append(figures)

    samples: dict[int, dict[str, list[float]]] = {}
    for (global_id, _meeting_id), speaker_figures in by_meeting.items():
        bucket = samples.setdefault(
            global_id, {"wpm": [], "pitch_spread": [], "pause_rate": []}
        )
        for metric, value in _meeting_sample(speaker_figures).items():
            if value is not None:
                bucket[metric].append(value)
    return samples


def _meeting_sample(speaker_figures: list[dict]) -> dict[str, float | None]:
    """One meeting's figures for a person, from each speaker linked to them.

    Several speakers linked to one person in one meeting are usually one voice
    that diarisation split, so together they are one sample holding all of the
    person's speech: pace and pitch movement as means weighted by each
    speaker's speech time, and pauses as the summed count over the summed
    speech. A single speaker's figures pass through unchanged.
    """
    timed = [f for f in speaker_figures if (f.get("speech_ms") or 0) > 0]
    speech_ms = sum(float(f["speech_ms"]) for f in timed)
    return {
        "wpm": _speech_weighted(speaker_figures, "words_per_minute"),
        "pitch_spread": _speech_weighted(speaker_figures, "pitch_spread_semitones"),
        "pause_rate": (
            sum(float(f.get("pause_count") or 0) for f in timed) / (speech_ms / 60_000)
            if speech_ms > 0
            else None
        ),
    }


def _speech_weighted(speaker_figures: list[dict], metric: str) -> float | None:
    """A metric across a meeting's speakers, weighted by their speech time.

    The stored figures are per-speaker medians and spreads, which cannot be
    pooled exactly; the weighted mean keeps a brief second speaker from
    counting as much as the rest of the person's speech. When none of them
    has a recorded speech time, they count equally.
    """
    measured = [
        (float(figures[metric]), max(float(figures.get("speech_ms") or 0), 0.0))
        for figures in speaker_figures
        if figures.get(metric)
    ]
    if not measured:
        return None
    if len(measured) == 1:
        return measured[0][0]
    total = sum(weight for _value, weight in measured)
    if total <= 0:
        return sum(value for value, _weight in measured) / len(measured)
    return sum(value * weight for value, weight in measured) / total


def _reduce(values: list[float], *, digits: int) -> float | int | None:
    """Median across meetings, or None below the minimum history."""
    if len(values) < BASELINE_MIN_MEETINGS:
        return None
    reduced = round(median(values), digits)
    return int(reduced) if digits == 0 else reduced
