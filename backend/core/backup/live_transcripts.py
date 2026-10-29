"""Render deferred live transcript snapshots inside the backup's read transaction."""

from collections.abc import Iterable, Mapping
from typing import Any

from sqlmodel import Session, select

from backend.models.transcript import Transcript
from backend.utils.canonical_pipeline import serialize_canonical_utterances
from backend.utils.speaker_names.durability import (
    restore_name_anchors,
    snapshot_name_anchors,
)


def project_live_transcript_rows(session: Session, rows: list[dict[str, Any]]) -> None:
    """Include current words and private evidence without writing the live snapshot.

    Canonical tables are deliberately excluded from format 3, so a live export
    must carry their durable word/protection evidence in the archived segments.
    All queries share the export's existing repeatable-read transaction.
    """
    for row in rows:
        state = row.get("settling_state") or {}
        if state.get("settled") is True:
            anchors = snapshot_name_anchors(session, row["recording_id"])
            if anchors:
                row["settling_state"] = {**state, "name_anchors": anchors}
        if (
            not state.get("eligible")
            or state.get("policy", {}).get("enabled") is not True
        ):
            continue
        segments = serialize_canonical_utterances(
            session,
            row["recording_id"],
            include_confidence_payload=True,
            resolve_merged_overlaps=False,
        )
        row["segments"] = segments
        row["text"] = " ".join(segment["text"] for segment in segments).strip()


def restore_transcript_name_anchors(
    session: Session,
    recording_ids: Iterable[int],
    global_speaker_ids: Mapping[int, int],
) -> None:
    """Remap archived person links before rebuilding durable naming acts.

    Only imported recordings are visited: a safe restore must leave existing
    target transcripts, including their runtime state, untouched.
    """
    for recording_id in sorted(recording_ids):
        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).first()
        if transcript is None:
            continue
        state = transcript.settling_state or {}
        if state.get("settled") is not True or not state.get("name_anchors"):
            continue
        anchors = [
            {
                **anchor,
                "global_speaker_id": global_speaker_ids.get(
                    anchor.get("global_speaker_id")
                ),
            }
            for anchor in state["name_anchors"]
        ]
        transcript.settling_state = {**state, "name_anchors": anchors}
        session.add(transcript)
        restore_name_anchors(session, recording_id)
