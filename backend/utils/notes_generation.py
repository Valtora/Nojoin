"""Per-output request ownership, independent of the recording lifecycle."""

from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlmodel import select

from backend.models.notes_output import NotesOutput
from backend.models.transcript import Transcript

NotesTarget = Transcript | NotesOutput


def lock_notes_target(session: Session, target: NotesTarget) -> NotesTarget:
    """Reload under the row lock before checking or changing a request claim."""
    return session.execute(
        select(type(target))
        .where(type(target).id == target.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def claim_notes_generation(session: Session, target: NotesTarget) -> str:
    target = lock_notes_target(session, target)
    request_id = uuid4().hex
    target.notes_generation_metadata = {
        **(target.notes_generation_metadata or {}),
        "request_id": request_id,
    }
    target.notes_status = "generating"
    target.set_notes_error_message(None)
    session.add(target)
    session.commit()
    return request_id


def owns_notes_generation(
    session: Session, target: NotesTarget, request_id: str
) -> bool:
    target = lock_notes_target(session, target)
    return (
        target.notes_status == "generating"
        and (target.notes_generation_metadata or {}).get("request_id") == request_id
    )


def start_notes_request(
    session: Session, target: NotesTarget, request_id: str | None
) -> str | None:
    """The run's request, claimed if it has none; None when a newer one owns it."""
    if request_id is None:
        request_id = claim_notes_generation(session, target)
    if not owns_notes_generation(session, target, request_id):
        session.rollback()
        return None
    session.commit()
    return request_id


def format_notes_generation_error(error: Exception | str) -> str:
    message = str(error).strip() or "Meeting notes could not be generated."
    if len(message) > 500:
        message = f"{message[:497]}..."
    return message


def fail_notes_generation(
    session: Session, target: NotesTarget, request_id: str, error: Exception | str
) -> None:
    if not owns_notes_generation(session, target, request_id):
        session.rollback()
        return
    target.notes_status = "error"
    target.set_notes_error_message(format_notes_generation_error(error))
    session.add(target)
    session.commit()


def complete_notes_metadata(target: NotesTarget, metadata: dict[str, Any]) -> None:
    target.notes_generation_metadata = {
        **(target.notes_generation_metadata or {}),
        **metadata,
    }
