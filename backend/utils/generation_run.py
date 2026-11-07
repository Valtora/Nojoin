"""One ownership rule for every writer of an identified generation run.

A run owns generation state only while its token matches a stored generating
claim. Result transactions take that fence before any ORM mutation or autoflush;
small progress/renewal transactions use exactly the same SQL predicate.
"""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import and_, false
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, select

from backend.models.transcript import Transcript
from backend.utils.generation_claim import (
    NOTES_RUN,
    RunKind,
    claim_generation,
    generation_active,
)
from backend.utils.notes_cancellation import NotesRunCancelled
from backend.utils.time import utc_now


def run_predicate(
    recording_id: int, run_id: str | UUID | None, kind: RunKind
) -> ColumnElement[bool]:
    """The stored ownership rule, including refusal of legacy tokenless tasks."""
    try:
        token = UUID(str(run_id))
    except (ValueError, TypeError, AttributeError):
        return false()
    return and_(
        col(Transcript.recording_id) == recording_id,
        getattr(Transcript, kind.status_field) == "generating",
        getattr(Transcript, kind.run_id_field) == token,
    )


def lock_transcript(session: Session, recording_id: int) -> Transcript | None:
    """Refresh the stored row under its lock before any generation-owned write."""
    with session.no_autoflush:
        return session.scalars(
            select(Transcript)
            .where(col(Transcript.recording_id) == recording_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()


def acquire_run(
    session: Session, recording_id: int, kind: RunKind
) -> Transcript | None:
    """Mint one claim under its lock; the caller commits and dispatches its token."""
    transcript = lock_transcript(session, recording_id)
    if transcript is None or generation_active(
        getattr(transcript, kind.status_field), transcript.updated_at
    ):
        return None
    claim_generation(transcript, kind)
    session.add(transcript)
    return transcript


def lock_owned_run(
    session: Session, recording_id: int, run_id: str | UUID | None, kind: RunKind
) -> Transcript | None:
    """Lock and freshly read only a claim still owned by the expected run."""
    with session.no_autoflush:
        return session.scalars(
            select(Transcript)
            .where(run_predicate(recording_id, run_id, kind))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()


def adopt_run(
    session: Session, recording_id: int, run_id: str | UUID | None, kind: RunKind
) -> Transcript | None:
    """Renew an owned queued claim at startup, preserving its immutable token."""
    transcript = lock_owned_run(session, recording_id, run_id, kind)
    if transcript is not None:
        transcript.updated_at = utc_now()
        session.add(transcript)
    return transcript


def retire_run(transcript: Transcript, kind: RunKind) -> None:
    """Release a locked claim without restoring a snapshot over independent edits."""
    setattr(transcript, kind.run_id_field, None)
    setattr(transcript, kind.progress_field, None)
    setattr(
        transcript,
        kind.status_field,
        "completed"
        if (getattr(transcript, kind.content_field) or "").strip()
        else "pending",
    )
    kind.clear_error(transcript)


def reset_notes_after_transcription(transcript: Transcript) -> None:
    """Final ASR never resets a live notes claim while publishing its transcript."""
    if generation_active(transcript.notes_status, transcript.updated_at):
        return
    if transcript.notes_status == "generating":
        retire_run(transcript, NOTES_RUN)
    else:
        transcript.notes_run_id = None
        transcript.notes_progress = None
        transcript.set_notes_error_message(None)
        if transcript.notes_status == "error":
            transcript.notes_status = "pending"


def run_check(
    session: Session, recording_id: int, run_id: str | UUID | None, kind: RunKind
) -> Callable[[], None]:
    """A request-boundary check with a fresh session on the worker's own engine."""
    bind = session.get_bind()

    def check() -> None:
        with Session(bind) as check_session:
            owned = check_session.execute(
                select(Transcript.id).where(run_predicate(recording_id, run_id, kind))
            ).scalar_one_or_none()
        if owned is None:
            raise NotesRunCancelled()

    return check
