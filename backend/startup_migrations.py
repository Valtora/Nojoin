from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from backend.core.db import sync_engine

logger = logging.getLogger(__name__)

AUTO_REPAIR_ENV_VAR = "NOJOIN_AUTO_REPAIR_MISSING_ALEMBIC_REVISIONS"
SKIP_STARTUP_ENV_VAR = "NOJOIN_SKIP_APP_STARTUP_MIGRATIONS"

TRUE_VALUES = {"1", "true", "yes", "on"}
REVISION_RE = re.compile(r"revision\s*(?::[^=]+)?=\s*['\"]([0-9a-f]+)['\"]")
DOWN_REVISION_RE = re.compile(r"down_revision\s*(?::[^=]+)?=\s*(.+)")
REVISION_ID_RE = re.compile(r"['\"]([0-9a-f]+)['\"]")

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI_PATH = REPO_ROOT / "alembic.ini"
ALEMBIC_VERSIONS_DIR = REPO_ROOT / "backend" / "alembic" / "versions"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUE_VALUES


def should_skip_startup_migrations() -> bool:
    return _env_flag(SKIP_STARTUP_ENV_VAR)


def _iter_version_files(versions_dir: Path = ALEMBIC_VERSIONS_DIR) -> list[Path]:
    return sorted(path for path in versions_dir.glob("*.py") if path.is_file())


def get_revision_graph(versions_dir: Path = ALEMBIC_VERSIONS_DIR) -> dict[str, tuple[str, ...]]:
    graph: dict[str, tuple[str, ...]] = {}

    for path in _iter_version_files(versions_dir):
        source = path.read_text(encoding="utf-8")
        revision_match = REVISION_RE.search(source)
        if revision_match is None:
            continue

        revision_id = revision_match.group(1)
        down_revision_match = DOWN_REVISION_RE.search(source)
        parent_ids: tuple[str, ...] = ()
        if down_revision_match is not None:
            parent_ids = tuple(REVISION_ID_RE.findall(down_revision_match.group(1)))

        graph[revision_id] = parent_ids

    return graph


def get_known_revision_ids(versions_dir: Path = ALEMBIC_VERSIONS_DIR) -> set[str]:
    return set(get_revision_graph(versions_dir))


def get_head_revision_ids(versions_dir: Path = ALEMBIC_VERSIONS_DIR) -> tuple[str, ...]:
    graph = get_revision_graph(versions_dir)
    referenced_revision_ids = {
        parent_id
        for parent_ids in graph.values()
        for parent_id in parent_ids
    }
    head_revision_ids = tuple(sorted(set(graph) - referenced_revision_ids))

    if not head_revision_ids:
        raise RuntimeError("Could not determine an Alembic head revision from the checked-in migration files.")

    return head_revision_ids


def get_database_revision_ids(connection: Connection) -> tuple[str, ...]:
    table_names = inspect(connection).get_table_names()
    if "alembic_version" not in table_names:
        return ()

    revision_ids = connection.execute(
        text("SELECT version_num FROM alembic_version ORDER BY version_num")
    ).scalars().all()
    return tuple(str(revision_id) for revision_id in revision_ids)


def repair_orphaned_revision_state(
    connection: Connection,
    *,
    auto_repair_enabled: bool,
    known_revision_ids: set[str] | None = None,
    head_revision_ids: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    known_revision_ids = known_revision_ids or get_known_revision_ids()
    head_revision_ids = head_revision_ids or get_head_revision_ids()

    database_revision_ids = get_database_revision_ids(connection)
    missing_revision_ids = tuple(
        revision_id
        for revision_id in database_revision_ids
        if revision_id not in known_revision_ids
    )
    if not missing_revision_ids:
        return ()

    if not auto_repair_enabled:
        missing_list = ", ".join(missing_revision_ids)
        head_list = ", ".join(head_revision_ids)
        raise RuntimeError(
            "Database is stamped to missing Alembic revision(s): "
            f"{missing_list}. This usually means a migration file was removed after this "
            "database had already been migrated. For local development, either reset the "
            f"database or set {AUTO_REPAIR_ENV_VAR}=true to restamp the current head "
            f"({head_list}). Do not enable auto-repair on persistent deployments."
        )

    logger.warning(
        "Database is stamped to missing Alembic revision(s) %s. Restamping current head(s) %s because %s is enabled.",
        ", ".join(missing_revision_ids),
        ", ".join(head_revision_ids),
        AUTO_REPAIR_ENV_VAR,
    )

    connection.execute(text("DELETE FROM alembic_version"))
    for head_revision_id in head_revision_ids:
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision_id)"),
            {"revision_id": head_revision_id},
        )
    connection.commit()

    return missing_revision_ids


def wait_for_database_connection(max_retries: int = 30, retry_interval: float = 1.0) -> None:
    logger.info("Waiting for database connection...")

    for attempt in range(max_retries):
        try:
            with sync_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            logger.info("Database connection established.")
            return
        except OperationalError:
            if attempt == max_retries - 1:
                break
            logger.info("Database not ready, retrying in %ss...", retry_interval)
            time.sleep(retry_interval)

    raise RuntimeError("Could not connect to the database after multiple retries.")


def run_startup_migrations() -> None:
    wait_for_database_connection()

    known_revision_ids = get_known_revision_ids()
    head_revision_ids = get_head_revision_ids()

    with sync_engine.connect() as connection:
        repair_orphaned_revision_state(
            connection,
            auto_repair_enabled=_env_flag(AUTO_REPAIR_ENV_VAR),
            known_revision_ids=known_revision_ids,
            head_revision_ids=head_revision_ids,
        )

    command.upgrade(Config(str(ALEMBIC_INI_PATH)), "head")


if __name__ == "__main__":
    run_startup_migrations()