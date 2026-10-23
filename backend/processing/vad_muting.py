"""Which final-pass stages hear the VAD-muted audio (#486).

With ``enable_vad`` on, the VAD stage writes two files of the same length and
rate: the normalised 16 kHz mono file, and a copy with every sample outside
speech set to zero. Muting never cuts audio, so both share one time base, and
each stage may read either. Transcription and the speaker side (diarisation,
the phantom filter and final-pass voiceprints) each have their own switch.

Free of heavy imports, so the API can name the keys without loading torch.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)

VAD_MUTE_TRANSCRIPTION_KEY = "vad_mute_transcription"
VAD_MUTE_DIARIZATION_KEY = "vad_mute_diarization"


@dataclass(frozen=True)
class MutedStages:
    """Whether each stage reads the muted file rather than the unmuted one."""

    transcription: bool
    diarization: bool


def _switch(config: Mapping[str, object], key: str) -> bool:
    """``config[key]`` when it is a bool, else the default (muted).

    Unset or null is the default. Any other value that is not a bool is logged
    and ignored: a processing run must not fail over a setting.
    """
    value = config.get(key)
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    logger.warning("Ignoring invalid %s=%r; muting that stage.", key, value)
    return True


def muted_stages(config: Mapping[str, object] | None) -> MutedStages:
    """The stages that read muted audio under ``config``.

    With ``enable_vad`` off nothing is muted, whatever the switches hold.
    """
    config = config or {}
    if not config.get("enable_vad", True):
        return MutedStages(transcription=False, diarization=False)
    return MutedStages(
        transcription=_switch(config, VAD_MUTE_TRANSCRIPTION_KEY),
        diarization=_switch(config, VAD_MUTE_DIARIZATION_KEY),
    )
