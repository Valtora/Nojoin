"""Render deferred live transcript snapshots inside the backup's read transaction."""

from typing import Any

from sqlmodel import Session

from backend.utils.canonical_pipeline import serialize_canonical_utterances


def project_live_transcript_rows(session: Session, rows: list[dict[str, Any]]) -> None:
    """Include current words and private evidence without writing the live snapshot.

    Canonical tables are deliberately excluded from format 3, so a live export
    must carry their durable word/protection evidence in the archived segments.
    All queries share the export's existing repeatable-read transaction.
    """
    for row in rows:
        state = row.get("settling_state") or {}
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
