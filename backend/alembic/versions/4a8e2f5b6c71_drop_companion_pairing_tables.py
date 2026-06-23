"""drop companion pairing tables

Revision ID: 4a8e2f5b6c71
Revises: 0a1f6b8e4c2d, 9f2d7c6b4a10, a7b4d3e1f920, c3f8a1d9e2b4, c7f4a9e2d1b3
Create Date: 2026-05-26 00:00:00.000000
"""

from __future__ import annotations

import logging
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)


revision: str = "4a8e2f5b6c71"
down_revision: Union[str, Sequence[str], None] = (
    "0a1f6b8e4c2d",
    "9f2d7c6b4a10",
    "a7b4d3e1f920",
    "c3f8a1d9e2b4",
    "c7f4a9e2d1b3",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ALLOW_DROP_ENV_VAR = "NOJOIN_ALLOW_COMPANION_DROP"
PAIRINGS_INDEXES = (
    "ix_companion_pairings_pairing_session_id",
    "ix_companion_pairings_status",
    "ix_companion_pairings_supersedes_pairing_session_id",
    "ix_companion_pairings_user_id",
)
PAIRING_REQUEST_INDEXES = (
    "ix_companion_pairing_requests_user_id",
    "ix_companion_pairing_requests_request_id",
    "ix_companion_pairing_requests_status",
    "ix_companion_pairing_requests_replacement_pairing_session_id",
    "ix_companion_pairing_requests_expires_at",
    "ix_companion_pairing_requests_completed_pairing_session_id",
)


def _allow_drop() -> bool:
    return os.getenv(ALLOW_DROP_ENV_VAR, "").strip() == "1"


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if not _allow_drop():
        logger.warning(
            "Skipping companion table drop. Set %s=1 to remove companion_pairings and companion_pairing_requests.",
            ALLOW_DROP_ENV_VAR,
        )
        return

    existing_tables = _table_names()

    if "companion_pairing_requests" in existing_tables:
        for index_name in PAIRING_REQUEST_INDEXES:
            op.execute(sa.text(f'DROP INDEX IF EXISTS "{index_name}"'))
        op.drop_table("companion_pairing_requests")

    if "companion_pairings" in existing_tables:
        for index_name in PAIRINGS_INDEXES:
            op.execute(sa.text(f'DROP INDEX IF EXISTS "{index_name}"'))
        op.drop_table("companion_pairings")


def downgrade() -> None:
    raise RuntimeError("Companion pairing table removal is irreversible.")
