from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from importlib import import_module

from sqlalchemy import text
from sqlmodel import Session

from backend.core.db import sync_engine
from backend.startup_migrations import wait_for_database_connection
from backend.utils.canonical_pipeline import (
    list_pending_startup_cutover_recording_ids,
    process_startup_cutover_recording,
)

logger = logging.getLogger(__name__)

SKIP_STARTUP_CANONICAL_CUTOVER_ENV_VAR = "NOJOIN_SKIP_STARTUP_CANONICAL_CUTOVER"
STARTUP_CANONICAL_CUTOVER_BATCH_SIZE_ENV_VAR = (
    "NOJOIN_STARTUP_CANONICAL_CUTOVER_BATCH_SIZE"
)
STARTUP_CANONICAL_CUTOVER_ADVISORY_LOCK_ID = 640_227_114_901_337_251
TRUE_VALUES = {"1", "true", "yes", "on"}
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
        logger.info(
            "Skipping startup canonical cutover because %s is enabled.",
            SKIP_STARTUP_CANONICAL_CUTOVER_ENV_VAR,
        )
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
    }

    # The advisory lock is held on a dedicated autocommit connection so no
    # transaction ever opens on it. Sessions below must NOT bind to that
    # connection: they would join its transaction via savepoints and every
    # commit would be silently rolled back when the connection closes.
    with sync_engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as lock_connection:
        with _advisory_lock(lock_connection):
            processed_ids: set[int] = set()
            while True:
                with Session(sync_engine) as session:
                    recording_ids = list_pending_startup_cutover_recording_ids(
                        session,
                        batch_size=_batch_size(),
                    )

                if not recording_ids:
                    break

                stalled_ids = processed_ids.intersection(recording_ids)
                if stalled_ids:
                    logger.warning(
                        "Startup canonical cutover made no progress on recordings %s; "
                        "stopping sweep to avoid looping at boot.",
                        sorted(stalled_ids),
                    )
                    break

                for recording_id in recording_ids:
                    with Session(sync_engine) as session:
                        outcome = process_startup_cutover_recording(
                            session,
                            recording_id=recording_id,
                        )
                        session.commit()
                    processed_ids.add(recording_id)
                    summary[outcome] = summary.get(outcome, 0) + 1

    logger.info("Startup canonical cutover complete: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_startup_canonical_cutover()
