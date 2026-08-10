"""Bind the deterministic metrics to canonical transcript rows.

Everything here runs synchronously inside ``db.run_sync``, matching how the
rest of the canonical pipeline is read from async handlers.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.models.pipeline import TranscriptUtterance
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.utils.canonical_pipeline import list_active_utterances

from .metrics import UtteranceRow, compute_deterministic_metrics
from .warnings import build_attribution_warning

logger = logging.getLogger(__name__)

UNKNOWN_SPEAKER_KEY = "unknown"


def _speaker_key(utterance: TranscriptUtterance) -> str:
    """A stable grouping key for one utterance's speaker.

    The recording-speaker id is preferred because it survives a rename, and a
    merge redirects every utterance onto the surviving row, so analytics follow
    a speaker correction with no invalidation step. Utterances that never got a
    speaker -- live-lane rows on a recording that was never finalised -- fall
    back to their label so they are visible as unattributed rather than dropped.
    """
    if utterance.recording_speaker_id is not None:
        return f"rs:{utterance.recording_speaker_id}"
    if utterance.speaker_label:
        return f"label:{utterance.speaker_label}"
    return UNKNOWN_SPEAKER_KEY


def _display_name(session, speaker: RecordingSpeaker) -> str:
    """Resolve a speaker's display name in the same order the transcript does."""
    if speaker.local_name:
        return speaker.local_name
    global_speaker = getattr(speaker, "global_speaker", None)
    if global_speaker is None and speaker.global_speaker_id:
        global_speaker = session.get(GlobalSpeaker, speaker.global_speaker_id)
    if global_speaker is not None and getattr(global_speaker, "name", None):
        return global_speaker.name
    if speaker.name:
        return speaker.name
    return speaker.diarization_label or "Unknown"


def _build_speaker_directory(session, recording_id: int) -> dict[str, dict[str, Any]]:
    """Map each grouping key to the identity the interface should show."""
    speakers = (
        session.query(RecordingSpeaker)
        .filter(RecordingSpeaker.recording_id == recording_id)
        .all()
    )
    directory: dict[str, dict[str, Any]] = {}
    for speaker in speakers:
        display_name = _display_name(session, speaker)
        directory[f"rs:{speaker.id}"] = {
            "speaker_key": f"rs:{speaker.id}",
            "public_id": speaker.public_id,
            "name": display_name,
            "diarization_label": speaker.diarization_label,
            "color": speaker.color,
            "global_speaker_id": speaker.global_speaker_id,
            # An unnamed speaker makes every figure attached to it much less
            # useful, so the interface needs to know to offer naming.
            #
            # Derived from the resolved name rather than from the presence of a
            # person link, because the two disagree: a link whose person has
            # since been deleted resolves back to the diarisation label, and
            # trusting the link would report "SPEAKER_00" as a named speaker.
            "is_named": bool(
                display_name and display_name != speaker.diarization_label
            ),
        }
    return directory


def _describe_unlisted_key(speaker_key: str) -> dict[str, Any]:
    """Identity for a key with no recording_speakers row behind it."""
    if speaker_key.startswith("label:"):
        label = speaker_key.split(":", 1)[1]
        return {
            "speaker_key": speaker_key,
            "public_id": None,
            "name": label,
            "diarization_label": label,
            "color": None,
            "global_speaker_id": None,
            "is_named": False,
        }
    return {
        "speaker_key": speaker_key,
        "public_id": None,
        "name": "Unattributed",
        "diarization_label": None,
        "color": None,
        "global_speaker_id": None,
        "is_named": False,
    }


def compute_recording_analytics(session, recording) -> dict[str, Any]:
    """Compute the deterministic analytics tier for one recording.

    Nothing is written. The tier is cheap enough to derive per read, which is
    what makes it correct on every historical recording without a backfill and
    immune to going stale after a transcript edit.
    """
    utterances = list_active_utterances(session, recording.id)
    rows = [
        UtteranceRow(
            speaker_key=_speaker_key(utterance),
            start_ms=int(utterance.start_ms or 0),
            end_ms=int(utterance.end_ms or 0),
        )
        for utterance in utterances
    ]

    duration_ms = int(round((recording.duration_seconds or 0) * 1000))
    metrics = compute_deterministic_metrics(rows, duration_ms)

    directory = _build_speaker_directory(session, recording.id)
    speakers = [
        directory.get(speaker_key) or _describe_unlisted_key(speaker_key)
        for speaker_key in sorted(
            metrics["talk_time"],
            key=lambda key: metrics["talk_time"][key]["speech_ms"],
            reverse=True,
        )
    ]

    return {
        "speakers": speakers,
        "metrics": metrics,
        "attribution_warning": build_attribution_warning(
            metrics=metrics,
            speakers=speakers,
            max_speakers=recording.max_speakers,
        ),
    }
