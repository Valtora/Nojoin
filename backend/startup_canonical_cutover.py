from __future__ import annotations

from importlib import import_module
import logging
import os
from contextlib import contextmanager

from sqlalchemy import or_, text
from sqlmodel import Session, select

from backend.core.db import sync_engine
from backend.models.task import UserTask
from backend.models.user import User
from backend.startup_migrations import wait_for_database_connection
from backend.utils.canonical_pipeline import (
    list_pending_startup_cutover_recording_ids,
    process_startup_cutover_recording,
)

logger = logging.getLogger(__name__)

SKIP_STARTUP_CANONICAL_CUTOVER_ENV_VAR = "NOJOIN_SKIP_STARTUP_CANONICAL_CUTOVER"
STARTUP_CANONICAL_CUTOVER_BATCH_SIZE_ENV_VAR = "NOJOIN_STARTUP_CANONICAL_CUTOVER_BATCH_SIZE"
STARTUP_CANONICAL_CUTOVER_ADVISORY_LOCK_ID = 640_227_114_901_337_251
TRUE_VALUES = {"1", "true", "yes", "on"}
COMPANION_RETIREMENT_NOTICE_TITLE = (
    "Companion app retired. Recording is now browser-only. See docs/CAPTURE.md."
)
MODEL_MODULES = (
    "backend.models.recording",
    "backend.models.speaker",
    "backend.models.tag",
    "backend.models.transcript",
    "backend.models.user",
    "backend.models.revoked_jwt",
    "backend.models.invitation",
    "backend.models.chat",
    "backend.models.document",
    "backend.models.context_chunk",
    "backend.models.people_tag",
    "backend.models.task",
    "backend.models.calendar",
    "backend.models.pipeline",
)


def _register_sqlmodel_models() -> None:
    for module_path in MODEL_MODULES:
        import_module(module_path)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUE_VALUES


def _batch_size() -> int:
    raw_value = os.getenv(STARTUP_CANONICAL_CUTOVER_BATCH_SIZE_ENV_VAR, "100").strip()
    try:
        parsed = int(raw_value)
    except ValueError:
        return 100
    return max(parsed, 1)


def _ensure_companion_retirement_notice(session: Session) -> int:
    existing_user_ids = set(
        session.exec(
            select(UserTask.user_id).where(
                UserTask.title == COMPANION_RETIREMENT_NOTICE_TITLE
            )
        ).all()
    )
    admin_users = session.exec(
        select(User).where(
            or_(
                User.role.in_(["owner", "admin"]),
                User.is_superuser == True,
            )
        )
    ).all()

    created = 0
    for admin_user in admin_users:
        if admin_user.id in existing_user_ids:
            continue
        session.add(
            UserTask(
                title=COMPANION_RETIREMENT_NOTICE_TITLE,
                user_id=admin_user.id,
            )
        )
        created += 1

    return created


@contextmanager
def _advisory_lock(connection):
    if connection.dialect.name != "postgresql":
        yield
        return

    connection.execute(
        text("SELECT pg_advisory_lock(:lock_id)"),
        {"lock_id": STARTUP_CANONICAL_CUTOVER_ADVISORY_LOCK_ID},
    )
    try:
        yield
    finally:
        connection.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": STARTUP_CANONICAL_CUTOVER_ADVISORY_LOCK_ID},
        )


def run_startup_canonical_cutover() -> dict[str, int]:
    if _env_flag(SKIP_STARTUP_CANONICAL_CUTOVER_ENV_VAR):
        logger.info("Skipping startup canonical cutover because %s is enabled.", SKIP_STARTUP_CANONICAL_CUTOVER_ENV_VAR)
        return {"skipped": 1}

    _register_sqlmodel_models()
    wait_for_database_connection()
    summary: dict[str, int] = {
        "backfilled": 0,
        "already_canonical": 0,
        "classified_inflight": 0,
        "classified_missing_transcript": 0,
        "classified_exception": 0,
        "already_backfilled": 0,
        "already_reprocess_required": 0,
        "skipped_unified": 0,
        "missing": 0,
        "retirement_notices": 0,
    }

    with sync_engine.connect() as connection:
        with _advisory_lock(connection):
            with Session(bind=connection) as session:
                summary["retirement_notices"] = _ensure_companion_retirement_notice(session)
                if summary["retirement_notices"]:
                    session.commit()

            while True:
                with Session(bind=connection) as session:
                    recording_ids = list_pending_startup_cutover_recording_ids(
                        session,
                        batch_size=_batch_size(),
                    )

                if not recording_ids:
                    break

                for recording_id in recording_ids:
                    with Session(bind=connection) as session:
                        outcome = process_startup_cutover_recording(
                            session,
                            recording_id=recording_id,
                        )
                        session.commit()
                    summary[outcome] = summary.get(outcome, 0) + 1

    logger.info("Startup canonical cutover complete: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_startup_canonical_cutover()