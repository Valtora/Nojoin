"""Bringing a version 2 archive's free-text user notes across as a timeline note.

Until format version 3 a recording's notes were one text field on its
transcript, ``transcripts.user_notes``. The column no longer exists, so the
restore drops the field with every other unknown one (``_adapt_record``). This
reads it first and, once the transcript row is actually inserted, adds the
'head' note the database migration makes of the same text. A transcript that
already exists in the target is not inserted, so restoring one archive twice
cannot add the note twice.
"""

import logging
from typing import Any, Dict, Optional

from sqlalchemy.exc import SQLAlchemyError

from backend.core.backup import runtime
from backend.core.backup.format import SKIP_REASON_INSERT_FAILED
from backend.core.backup.restore.context import _RowContext
from backend.models.timeline_note import TimelineNoteKind

logger = logging.getLogger(__name__)

LEGACY_NOTES_TABLE = "transcripts"
LEGACY_NOTES_FIELD = "user_notes"


def legacy_user_notes(table_name: str, raw_row: Dict[str, Any]) -> Optional[str]:
    """The archived free-text notes of a transcript row, stripped, if any."""
    if table_name != LEGACY_NOTES_TABLE:
        return None
    value = raw_row.get(LEGACY_NOTES_FIELD)
    if not isinstance(value, str):
        return None
    return value.strip() or None


def restore_legacy_head_note(ctx: _RowContext, body: str) -> None:
    """Add the head note for a just-inserted transcript's archived notes."""
    recording_id = ctx.item_data["recording_id"]
    recording = ctx.session.get(runtime.Recording, recording_id)
    noted_at = ctx.item_data.get("updated_at")
    note = runtime.TimelineNote.model_validate(
        {
            "recording_id": recording_id,
            "created_by_user_id": recording.user_id if recording else None,
            "kind": TimelineNoteKind.HEAD.value,
            "body": body,
            **({"created_at": noted_at, "updated_at": noted_at} if noted_at else {}),
        }
    )
    try:
        with ctx.session.begin_nested():
            ctx.session.add(note)
            ctx.session.flush()
    except SQLAlchemyError as insert_err:
        # Counted and skipped inside its savepoint, as for any restored row.
        logger.error(
            "Failed to restore the archived notes of recording %s as a note: %s. "
            "Skipping.",
            recording_id,
            insert_err,
        )
        ctx.state.record_skip("timeline_notes", SKIP_REASON_INSERT_FAILED)
