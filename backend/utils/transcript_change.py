"""The counter a finished recording's page polls to notice transcript changes.

Writers that change what a transcript reader shows (lines, speakers, names)
execute this in their own transaction. It is a hint, not a cursor: a writer
that forgets it leaves an idle page stale until it reloads.
"""

from __future__ import annotations

from sqlalchemy import Update, update
from sqlmodel import col

from backend.models.transcript import Transcript


def change_sequence_bump(*recording_ids: int) -> Update:
    """The UPDATE that advances each recording's transcript change counter."""
    return (
        update(Transcript)
        .where(col(Transcript.recording_id).in_(recording_ids))
        .values(change_sequence=col(Transcript.change_sequence) + 1)
    )
