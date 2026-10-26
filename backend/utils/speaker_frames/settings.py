"""The two ``config.json`` keys that govern the screen study (#653).

``speaker_frame_sampling`` is ``off`` (the default) or ``owner``: only then may
the primary Owner tick **Screen study** at Start Meeting, and only then do the
endpoints answer. ``speaker_frame_rate_hz`` is how often the browser samples,
1 (the default) or 2. Any other value of either key reads as the default, so a
typo can only switch the study off or slow it down.

Neither key has a settings page. The API re-reads ``config.json`` here, at the
cost of one ``stat`` when the file is unchanged, so an operator's edit reaches
the next request without a restart.
"""

from __future__ import annotations

from backend.utils.config_manager import config_manager

SPEAKER_FRAME_SAMPLING_KEY = "speaker_frame_sampling"
SPEAKER_FRAME_SAMPLING_OFF = "off"
SPEAKER_FRAME_SAMPLING_OWNER = "owner"

SPEAKER_FRAME_RATE_KEY = "speaker_frame_rate_hz"
ALLOWED_FRAME_RATES_HZ = (1, 2)
DEFAULT_FRAME_RATE_HZ = 1


def sampling_mode() -> str:
    """``owner`` when the key says so exactly, ignoring case; otherwise ``off``."""
    config_manager.reload()
    raw = config_manager.get(SPEAKER_FRAME_SAMPLING_KEY, SPEAKER_FRAME_SAMPLING_OFF)
    mode = str(raw or SPEAKER_FRAME_SAMPLING_OFF).strip().lower()
    return (
        SPEAKER_FRAME_SAMPLING_OWNER
        if mode == SPEAKER_FRAME_SAMPLING_OWNER
        else SPEAKER_FRAME_SAMPLING_OFF
    )


def sampling_enabled() -> bool:
    return sampling_mode() == SPEAKER_FRAME_SAMPLING_OWNER


def frame_rate_hz() -> int:
    """1 or 2. Anything else, a boolean or a string included, reads as 1."""
    raw = config_manager.get(SPEAKER_FRAME_RATE_KEY, DEFAULT_FRAME_RATE_HZ)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULT_FRAME_RATE_HZ
    return raw if raw in ALLOWED_FRAME_RATES_HZ else DEFAULT_FRAME_RATE_HZ
