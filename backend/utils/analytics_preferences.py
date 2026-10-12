"""Per-user choices about the Meeting Analytics tiers.

Both are read the way the other per-user booleans are: the user's own value
wins, an unset value falls to the install's ``config.json``, and then to the
shipped default. Kept apart from ``config_manager`` only for that module's
size; the keys and defaults still live there.
"""

from __future__ import annotations

from backend.utils.config_manager import config_manager, get_default_user_settings


def _user_or_install_bool(settings: dict | None, key: str, default: bool) -> bool:
    user_value = (settings or {}).get(key)
    if user_value is not None:
        return bool(user_value)
    defaults = get_default_user_settings()
    return bool(config_manager.get(key, defaults.get(key, default)))


def is_auto_remeasure_delivery_enabled(settings: dict | None) -> bool:
    """Whether stale delivery figures are re-measured on the next Analytics read."""
    return _user_or_install_bool(settings, "auto_remeasure_delivery", True)


def is_auto_analyse_meetings_enabled(settings: dict | None) -> bool:
    """Whether a meeting is queued for AI analysis when processing finishes."""
    return _user_or_install_bool(settings, "auto_analyse_meetings", False)
