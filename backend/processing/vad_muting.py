"""Which final-pass stages hear the VAD-muted audio (#486).

With ``enable_vad`` on, the VAD stage writes two files of the same length and
rate: the normalised 16 kHz mono file, and a copy with every sample outside
speech set to zero. Muting never cuts audio, so both share one time base, and
each stage may read either. Transcription and the speaker side (diarisation,
the phantom filter and final-pass voiceprints) each have their own switch.
Transcription hears the muted file by default and the speaker side the
unmuted one.

Diarisation runs first, so muted transcription can hear a third file (#670):
the muted one with the unmuted audio put back inside every diarised turn but
those the phantom filter confirmed as non-speech (#678). It is zero only where
neither VAD nor diarisation heard speech.

Free of heavy imports, so the API can name the keys without loading torch.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Hashable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray
    from pyannote.core import Annotation, Segment

logger = logging.getLogger(__name__)

# The first and last second the VAD stage always mutes, which hold recording
# artefacts (mute_non_speech_segments' defaults). They stay muted for
# transcription even inside a turn.
VAD_EDGE_MUTE_MS = 1000

# Frames read per step when writing the union, so a long recording is never
# held in memory whole: about 65 s at 16 kHz.
_UNION_BLOCK_FRAMES = 1 << 20

VAD_MUTE_TRANSCRIPTION_KEY = "vad_mute_transcription"
VAD_MUTE_DIARIZATION_KEY = "vad_mute_diarization"

# The shipped defaults, also the user defaults in config_manager. The worker
# merge does not apply user defaults, so a user with no stored value reaches
# _switch with the key unset and gets these.
VAD_MUTE_DEFAULTS: dict[str, bool] = {
    VAD_MUTE_TRANSCRIPTION_KEY: True,
    VAD_MUTE_DIARIZATION_KEY: False,
}


@dataclass(frozen=True)
class MutedStages:
    """Whether each stage reads the muted file rather than the unmuted one."""

    transcription: bool
    diarization: bool


def _switch(config: Mapping[str, object], key: str) -> bool:
    """``config[key]`` when it is a bool, else the key's shipped default.

    Unset or null is the default. Any other value that is not a bool is logged
    and ignored: a processing run must not fail over a setting.
    """
    default = VAD_MUTE_DEFAULTS[key]
    value = config.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    logger.warning(
        "Ignoring invalid %s=%r; using the default, %s.", key, value, default
    )
    return default


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


def diarised_turns(
    diarization: Annotation, excluding: Collection[tuple[Segment, Hashable]] = ()
) -> list[tuple[float, float]]:
    """(start, end) seconds of every turn in a pyannote ``Annotation``.

    ``excluding`` names turns to leave out by their ``(segment, track)`` key, so
    another speaker's turn with the same bounds is kept.
    """
    left_out = set(excluding)
    return [
        (float(turn.start), float(turn.end))
        for turn, track in diarization.itertracks()
        if (turn, track) not in left_out
    ]


def _turn_frames(
    turns: Iterable[tuple[float, float]], rate: int, total: int, edge: int
) -> list[tuple[int, int]]:
    """Each turn as a sorted [begin, end) frame span, clipped to the unmuted edges."""
    spans = []
    for start, end in turns:
        begin = max(round(start * rate), edge)
        stop = min(round(end * rate), total - edge)
        if stop > begin:
            spans.append((begin, stop))
    return sorted(spans)


def _inside_turns(
    spans: list[tuple[int, int]], offset: int, length: int
) -> NDArray[np.bool_]:
    """Mask of the frames ``offset`` to ``offset + length`` that lie in a span."""
    import numpy as np

    inside = np.zeros(length, dtype=bool)
    for begin, stop in spans:
        if begin >= offset + length:
            break
        if stop > offset:
            inside[max(begin - offset, 0) : stop - offset] = True
    return inside


def write_union_muted_audio(
    unmuted_path: str,
    muted_path: str,
    output_path: str,
    turns: Iterable[tuple[float, float]],
    edge_mute_ms: int = VAD_EDGE_MUTE_MS,
) -> float:
    """Write the muted file with the unmuted audio put back inside every turn.

    Outside every turn, and in the first and last ``edge_mute_ms``, the result
    is the muted file; inside a turn it is the unmuted file. With the default
    "mute" silence method the muted file's speech equals the unmuted file, so
    the result is zero only where VAD heard no speech and no turn lies. With
    "fade", VAD speech inside a turn comes back unfaded. Returns the seconds of
    audio put back.

    Raises ``ValueError`` when the two files differ in rate, channels or
    length, and what soundfile raises for a file it cannot read or write.
    """
    import numpy as np
    import soundfile

    with (
        soundfile.SoundFile(unmuted_path) as heard,
        soundfile.SoundFile(muted_path) as muted,
    ):
        shape = (muted.samplerate, muted.channels, muted.frames)
        if (heard.samplerate, heard.channels, heard.frames) != shape:
            raise ValueError(f"{unmuted_path} and {muted_path} differ in shape")
        rate, channels, total = shape
        spans = _turn_frames(turns, rate, total, round(edge_mute_ms * rate / 1000))
        restored = offset = 0
        with soundfile.SoundFile(
            output_path, "w", rate, channels, muted.subtype, format=muted.format
        ) as out:
            # int32 reads and writes any PCM subtype without rounding.
            for kept, full in zip(
                muted.blocks(_UNION_BLOCK_FRAMES, dtype="int32", always_2d=True),
                heard.blocks(_UNION_BLOCK_FRAMES, dtype="int32", always_2d=True),
            ):
                inside = _inside_turns(spans, offset, len(kept))
                opened = inside & ~kept.any(axis=1) & full.any(axis=1)
                restored += int(np.count_nonzero(opened))
                out.write(np.where(inside[:, None], full, kept))
                offset += len(kept)
    return restored / rate
