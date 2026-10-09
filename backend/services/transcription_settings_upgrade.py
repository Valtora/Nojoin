"""Carry the owner's transcription choice into config.json on upgrade.

Before the transcription engine and model were install-wide, the owner's choice
lived on the owner's own account: Settings > Transcription stored it there, and
first-run setup stored the wizard's Whisper size there. config.json held what
everyone else used. Every reader now takes these keys from config.json alone, so
without this step the owner's engine would silently revert to the config.json
value on upgrade.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.user import User
from backend.utils.config_manager import (
    DEFAULT_SYSTEM_CONFIG,
    TRANSCRIPTION_BACKENDS,
    TRANSCRIPTION_SETTING_KEYS,
    WHISPER_MODEL_SIZES,
    config_manager,
)

logger = logging.getLogger(__name__)


def _usable(key: str, value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if key == "transcription_backend":
        return value in TRANSCRIPTION_BACKENDS
    if key == "whisper_model_size":
        return value in WHISPER_MODEL_SIZES
    return True


def _choose(key: str, owner_value: Any, file_value: Any) -> bool:
    """Whether the owner's value replaces config.json's for this key, logged.

    The owner's value wins, with one exception: an owner value equal to the
    shipped default does not replace a config.json value that differs from it.
    First-run setup has stored the wizard's Whisper size, usually the default,
    on the owner's account, so that default may never have been a choice, while
    the config.json value was set by an operator or by an older release.
    """
    default = DEFAULT_SYSTEM_CONFIG[key]
    current = default if file_value in (None, "") else file_value
    if not _usable(key, owner_value):
        logger.warning(
            "The owner's %s, %r, is not a valid choice, so config.json keeps %r.",
            key,
            owner_value,
            current,
        )
        return False
    if owner_value == current:
        return False
    if current == default:
        return True
    if owner_value == default:
        logger.warning(
            "config.json keeps %s %r: the owner's %r is the shipped default, "
            "which first-run setup also stores, so it is not treated as a choice.",
            key,
            current,
            owner_value,
        )
        return False
    logger.warning(
        "config.json's %s %r is replaced by the owner's choice, %r.",
        key,
        current,
        owner_value,
    )
    return True


async def carry_owner_transcription_choice(session: AsyncSession) -> dict[str, str]:
    """Write the owner's transcription keys to config.json, then clear their row.

    The owner is the first account with the owner role, by id: the account the
    install-wide LLM settings already fall back to. Other rows are not carried,
    and are ignored from now on. See _choose for which value wins.

    Idempotent: once the keys are cleared from the owner's row, a later run
    finds nothing to carry, and so never overwrites a choice an administrator
    has saved since. A config.json that does not hold a JSON object is left
    alone, and a failed write raises before the row is touched, so the next
    start tries again. Returns the values written to config.json.
    """
    result = await session.execute(
        select(User).where(User.role == "owner").order_by(User.id).limit(1)
    )
    owner = result.scalars().first()
    row = dict(owner.settings or {}) if owner is not None else {}
    held = [key for key in TRANSCRIPTION_SETTING_KEYS if key in row]
    if owner is None or not held:
        return {}

    on_disk = config_manager.read_file()
    if on_disk is None:
        logger.warning(
            "config.json could not be read, so the owner's transcription choice "
            "stays on their account, unused, until the next start."
        )
        return {}

    carried = {
        key: row[key] for key in held if _choose(key, row[key], on_disk.get(key))
    }
    if carried:
        config_manager.save_values(carried)

    owner.settings = {
        key: value
        for key, value in row.items()
        if key not in TRANSCRIPTION_SETTING_KEYS
    }
    session.add(owner)
    await session.commit()
    return carried
