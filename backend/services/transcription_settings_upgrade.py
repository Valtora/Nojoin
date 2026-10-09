"""Carry the owner's transcription choice into config.json on upgrade.

Before the transcription engine and model were install-wide, Settings >
Transcription stored an administrator's choice on that administrator's own
account, and config.json held what everyone else used. Every reader now takes
these keys from config.json alone, so without this step the owner's engine
would silently revert to the config.json value on upgrade.
"""

from __future__ import annotations

import json
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


def _read_config_file(path: str) -> dict[str, Any] | None:
    """config.json as it is on disk, without the defaults the loader fills in.

    None when the file exists but cannot be parsed, so that nothing is written
    over a file an operator is part-way through editing.
    """
    try:
        with open(path, encoding="utf-8") as config_file:
            on_disk = json.load(config_file)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return on_disk if isinstance(on_disk, dict) else None


def _set_by_operator(on_disk: dict[str, Any], key: str) -> bool:
    """Whether config.json holds a value for this key that someone chose.

    Nothing in the UI wrote these keys to config.json before this release, and
    the file is written with every default on first start. So a value other
    than the shipped default was set by an operator, while the default itself
    cannot be told apart from one nobody set.
    """
    return on_disk.get(key) not in (None, "", DEFAULT_SYSTEM_CONFIG[key])


def _usable(key: str, value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if key == "transcription_backend":
        return value in TRANSCRIPTION_BACKENDS
    if key == "whisper_model_size":
        return value in WHISPER_MODEL_SIZES
    return True


async def carry_owner_transcription_choice(session: AsyncSession) -> dict[str, str]:
    """Write the owner's transcription keys to config.json, then clear their row.

    For each key, a value an operator set in config.json wins, then the
    owner's, then the shipped default. Only the owner's row is carried: it is
    the account the install-wide LLM settings already fall back to, and other
    rows are ignored from now on.

    Idempotent: once the keys are cleared from the owner's row, a later run
    finds nothing to carry, and so never overwrites a choice an administrator
    has saved since. A failed write raises before the row is touched, so the
    next start tries again. Returns the values written to config.json.
    """
    result = await session.execute(select(User).where(User.role == "owner"))
    owner = result.scalar_one_or_none()
    row = dict(owner.settings or {}) if owner is not None else {}
    held = [key for key in TRANSCRIPTION_SETTING_KEYS if key in row]
    if owner is None or not held:
        return {}

    on_disk = _read_config_file(config_manager.config_path)
    if on_disk is None:
        logger.warning(
            "config.json could not be read, so the owner's transcription choice "
            "stays on their account until the next start."
        )
        return {}

    carried: dict[str, str] = {}
    for key in held:
        value = row[key]
        current = on_disk.get(key, DEFAULT_SYSTEM_CONFIG[key])
        if not _usable(key, value) or value == current:
            continue
        if _set_by_operator(on_disk, key):
            logger.warning(
                "config.json sets %s to %r, so the owner's %r was not carried over.",
                key,
                current,
                value,
            )
            continue
        carried[key] = value

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
