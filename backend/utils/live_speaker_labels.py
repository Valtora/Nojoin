"""Live speaker labels as they are stored, and as every reader shows them.

The live label step stores ``LIVE_PROVISIONAL_<n>`` in
``TranscriptUtterance.speaker_label``, a namespace of its own, so a live label
is never taken for a diarisation label (``SPEAKER_nn``), for a person, or for
the older ``LIVE_<nn>`` labels, which the canonical pipeline makes speakers of.
No ``RecordingSpeaker`` names it, so every reader that resolves labels through
the recording's speakers shows it as "Speaker <n>", as the live panel does.

Standard library only: the API imports it, and its image has no SciPy or torch.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

LIVE_SPEAKER_LABEL_PREFIX = "LIVE_PROVISIONAL_"
_LIVE_SPEAKER_LABEL = re.compile(r"^LIVE_PROVISIONAL_(\d+)$")


def live_speaker_label(number: int) -> str:
    """The stored label of live speaker ``number`` (counted from 1)."""
    return f"{LIVE_SPEAKER_LABEL_PREFIX}{number}"


def live_speaker_display_name(label: object) -> str | None:
    """ "Speaker <n>" for a live label, None for any other label."""
    match = _LIVE_SPEAKER_LABEL.match(str(label or ""))
    return f"Speaker {match.group(1)}" if match else None


def speaker_display_name(label: str, speaker_map: Mapping[str, str]) -> str:
    """The name a reader shows for ``label``.

    The recording's own speaker name first, then "Speaker <n>" for a live
    label, else the label itself, as readers did before live labels.
    """
    if label in speaker_map:
        return speaker_map[label]
    return live_speaker_display_name(label) or label


def prompt_speaker_label(label: str) -> str:
    """``label`` as a prompt shows it: UNKNOWN reads as an unidentified speaker."""
    return "Unidentified speaker" if label == "UNKNOWN" else label


def speaker_prompt_name(label: str, speaker_map: Mapping[str, str]) -> str:
    """Resolve a prompt label without presenting UNKNOWN as a person's name."""
    if label == "UNKNOWN":
        return prompt_speaker_label(label)
    return speaker_display_name(label, speaker_map)


def render_indexed_transcript_for_llm(text: str, speaker_map: Mapping[str, str]) -> str:
    """Resolve indexed transcript line labels, leaving spoken text untouched.

    Older chunks can still carry UNKNOWN or raw diarisation labels. Resolve
    those at read time so they do not require a library-wide reindex.
    """
    names = {
        label: speaker_prompt_name(label, speaker_map)
        for label in {"UNKNOWN", *speaker_map}
        if label
    }
    pattern = re.compile(
        r"^(" + "|".join(re.escape(label) for label in names) + r"):", re.MULTILINE
    )
    return pattern.sub(lambda match: f"{names[match.group(1)]}:", text)
