"""Validated installation settings for live word settling."""

import math
from typing import Any

LIVE_SETTLING_DEFAULTS: dict[str, Any] = {
    "live_text_settling": "auto",
    "live_text_settling_k": 3,
    "live_text_settling_window_s": 10.0,
}
LIVE_SETTLING_KEYS = tuple(LIVE_SETTLING_DEFAULTS)
# off; auto: GPU only; always: GPU and CPU, where each step re-decodes on the CPU.
LIVE_SETTLING_MODES = ("off", "auto", "always")


def validate_live_settling_setting(key: str, value: Any) -> None:
    """Reject invalid policy values before they reach capture state.

    Raises:
        ValueError: The setting is outside the supported policy bounds.
    """
    if key == "live_text_settling":
        valid = isinstance(value, str) and value in LIVE_SETTLING_MODES
    elif key == "live_text_settling_k":
        valid = type(value) is int and 1 <= value <= 10
    else:
        valid = (
            type(value) in {int, float} and math.isfinite(value) and 2 <= value <= 30
        )
    if not valid:
        raise ValueError(f"Invalid {key}.")


def normalise_live_settling_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Use safe defaults for old, hand-edited or restored invalid settings."""
    result = {}
    for key, default in LIVE_SETTLING_DEFAULTS.items():
        value = config.get(key, default)
        try:
            validate_live_settling_setting(key, value)
        except ValueError:
            value = default
        result[key] = value
    return result
