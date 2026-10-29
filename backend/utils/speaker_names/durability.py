"""Durable human naming facts, independent of excluded audit tables."""

from copy import deepcopy
from typing import Any

from sqlmodel import select

from backend.models.speaker import RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.utils.canonical_pipeline.constants import _load_transcript

from .sources import AUTHORITY, KIND_DISMISS, SOURCE_USER


def snapshot_name_anchors(session: Any, recording_id: int) -> list[dict[str, Any]]:
    """Return standing human naming acts with stable speaker identities.

    The person FK is remapped by format-3 restore, like the same FK on an
    archived RecordingSpeaker. Event integer IDs and processing-run IDs are
    deliberately absent. Dismissals stand too, so an old suggestion stays
    dismissed after the archive's audit history has gone.
    """
    rows = list(
        session.execute(
            select(SpeakerNameEvent)
            .where(SpeakerNameEvent.recording_id == recording_id)
            .order_by(SpeakerNameEvent.id)
        )
        .scalars()
        .all()
    )
    superseded = {
        row.supersedes_event_id for row in rows if row.supersedes_event_id is not None
    }
    speakers = {
        speaker.id: speaker.public_id
        for speaker in session.execute(
            select(RecordingSpeaker).where(
                RecordingSpeaker.recording_id == recording_id
            )
        )
        .scalars()
        .all()
    }
    anchors = [
        {
            "public_id": row.public_id,
            "kind": row.kind,
            "name": row.name,
            "global_speaker_id": row.global_speaker_id,
            "surface": row.surface,
            "anchor_start_ms": row.anchor_start_ms,
            "anchor_end_ms": row.anchor_end_ms,
            "anchor_label": row.anchor_label,
            "recording_speaker_public_id": speakers.get(row.recording_speaker_id),
        }
        for row in rows
        if row.source == SOURCE_USER
        and (row.kind == KIND_DISMISS or row.id not in superseded)
    ]
    if not rows:
        transcript = _load_transcript(session, recording_id)
        return (
            deepcopy((transcript.settling_state or {}).get("name_anchors", []))
            if transcript
            else []
        )
    return anchors


def persist_name_anchors(session: Any, recording_id: int) -> None:
    """Save human acts in the Transcript publication transaction."""
    transcript = _load_transcript(session, recording_id, lock=True)
    if (
        transcript is None
        or (transcript.settling_state or {}).get("settled") is not True
    ):
        return
    transcript.settling_state = {
        **transcript.settling_state,
        "name_anchors": snapshot_name_anchors(session, recording_id),
    }
    session.add(transcript)


def restore_name_anchors(session: Any, recording_id: int) -> None:
    """Rebuild excluded name rows once, after archived person IDs are remapped."""
    if not hasattr(session, "execute"):
        return
    transcript = _load_transcript(session, recording_id, lock=True)
    if (
        transcript is None
        or (transcript.settling_state or {}).get("settled") is not True
    ):
        return
    anchors = (transcript.settling_state or {}).get("name_anchors") or []
    existing = set(
        session.execute(
            select(SpeakerNameEvent.public_id).where(
                SpeakerNameEvent.recording_id == recording_id
            )
        )
        .scalars()
        .all()
    )
    speakers = {
        speaker.public_id: speaker
        for speaker in session.execute(
            select(RecordingSpeaker).where(
                RecordingSpeaker.recording_id == recording_id
            )
        )
        .scalars()
        .all()
    }
    for anchor in anchors:
        if anchor["public_id"] in existing:
            continue
        speaker = speakers.get(anchor.get("recording_speaker_public_id"))
        session.add(
            SpeakerNameEvent(
                public_id=anchor["public_id"],
                recording_id=recording_id,
                kind=anchor["kind"],
                name=anchor["name"],
                global_speaker_id=anchor.get("global_speaker_id"),
                source=SOURCE_USER,
                authority=AUTHORITY[SOURCE_USER],
                surface=anchor.get("surface"),
                anchor_start_ms=anchor["anchor_start_ms"],
                anchor_end_ms=anchor["anchor_end_ms"],
                anchor_label=anchor.get("anchor_label"),
                recording_speaker_id=speaker.id if speaker else None,
            )
        )
        existing.add(anchor["public_id"])
    session.flush()
