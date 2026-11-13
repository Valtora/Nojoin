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
    "their usual".
    """
    by_global: dict[int, str] = {}
    for speaker in speakers:
        global_id = speaker.get("global_speaker_id")
        if global_id is not None:
            by_global[int(global_id)] = speaker["speaker_key"]
    if not by_global:
        return {}

    # The person's speakers in the meetings their People entry counts: owned,
    # not in the bin, and through a speaker counted_speaker() keeps.
    statement = (
        select(
            col(RecordingSpeaker.global_speaker_id),
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
        baselines[by_global[global_id]] = {
            "meetings": meetings,
            "words_per_minute": _reduce(bucket["wpm"], digits=0),
            "pitch_spread_semitones": _reduce(bucket["pitch_spread"], digits=2),
            "pauses_per_minute": _reduce(bucket["pause_rate"], digits=2),
        }
    return baselines


def _collect_samples(rows) -> dict[int, dict[str, list[float]]]:
    """Per-person metric samples, one value per comparable meeting."""
    samples: dict[int, dict[str, list[float]]] = {}
    for global_id, recording_speaker_id, payload in rows:
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
        bucket = samples.setdefault(
            int(global_id), {"wpm": [], "pitch_spread": [], "pause_rate": []}
        )
        if figures.get("words_per_minute"):
            bucket["wpm"].append(float(figures["words_per_minute"]))
        if figures.get("pitch_spread_semitones"):
            bucket["pitch_spread"].append(float(figures["pitch_spread_semitones"]))
        speech_ms = figures.get("speech_ms") or 0
        if speech_ms > 0:
            bucket["pause_rate"].append(
                float(figures.get("pause_count") or 0) / (speech_ms / 60_000)
            )
    return samples


def _reduce(values: list[float], *, digits: int) -> float | int | None:
    """Median across meetings, or None below the minimum history."""
    if len(values) < BASELINE_MIN_MEETINGS:
        return None
    reduced = round(median(values), digits)
    return int(reduced) if digits == 0 else reduced
