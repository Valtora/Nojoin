"""Per-user choices about speaker names.

Read the way the other per-user settings are: the user's own value wins, an
unset value falls to the install's ``config.json``, and then to the shipped
default in ``DEFAULT_USER_SETTINGS``.
"""

from __future__ import annotations

from typing import Any

from backend.utils.config_manager import config_manager, get_default_user_settings

CONFIRM_SUGGESTED_NAMES_KEY = "confirm_suggested_names"


def _user_or_install(settings: dict[str, Any] | None, key: str) -> Any:
    user_value = (settings or {}).get(key)
    if user_value is not None:
        return user_value
    return config_manager.get(key, get_default_user_settings().get(key))


def confirm_suggested_names_enabled(settings: dict[str, Any] | None) -> bool:
    """Whether inferred names are taken as given when processing finishes."""
    return bool(_user_or_install(settings, CONFIRM_SUGGESTED_NAMES_KEY))
