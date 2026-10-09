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
    """False for an unknown engine or Whisper size.

    Model ids are free-form, as the settings API has always accepted them:
    onnx-asr is handed whatever id is configured.
    """
    if not isinstance(value, str):
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
    A default on the owner's row may never have been a choice: first-run setup
    stored the wizard's Whisper size there, and the settings page saves every
    displayed value, defaults included, whenever an administrator changes any
    setting. The config.json value may have been set by an operator or by an
    older release. A non-default value on the row wins even over a later hand
    edit of the file, since it is the owner's effective choice.

    An empty owner value is skipped quietly; an unknown engine or Whisper size
    is skipped with a warning.
    """
    default = DEFAULT_SYSTEM_CONFIG[key]
    current = default if file_value in (None, "") else file_value
    if owner_value in (None, ""):
        return False
    if not _usable(key, owner_value):
        logger.warning(
            "The owner's %s, %r, is not a known engine or Whisper size, so "
            "config.json keeps %r.",
            key,
            owner_value,
            current,
        )
        return False
    if owner_value == current:
        return False
    if owner_value == default:
        logger.warning(
            "config.json keeps %s %r: the owner's %r is the shipped default, "
            "which may never have been a choice (setup and the settings page "
            "store defaults on the owner's account).",
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

    The owner is the first account with the owner role, by id. Other rows are
    not carried, and are ignored from now on. See _choose for which value wins.

    Idempotent: once the keys are cleared from the owner's row, a later run
    finds nothing to carry, and so never overwrites a choice an administrator
    has saved since. A config.json that cannot be read, or is not a JSON
    object, is left alone, and a failed write raises before the row is touched, so the next
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

    try:
        on_disk = config_manager.read_file()
    except (OSError, ValueError) as exc:
        logger.warning(
            "The owner's transcription choice stays on their account, unused, "
            "until the next start: %s",
            exc,
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
