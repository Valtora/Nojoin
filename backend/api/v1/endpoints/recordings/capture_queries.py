"""Query parameters the capture lifecycle routes share.

Each is bounded to the column it is checked against or stored in: a capture
epoch to ``recordings.capture_epoch``, an INTEGER, and a sequence to the BIGINT
``recording_audio_chunks.sequence_no``. A larger value is none the server could
have issued or stored, so validation refuses it with the usual 422 (#927).
"""

from typing import Annotated, Optional

from fastapi import Query

from backend.api.params import BIGINT_MAX, INTEGER_MAX


def capture_epoch_query(action: str):
    return Query(
        None,
        ge=0,
        le=INTEGER_MAX,
        description=(
            f"The capture epoch of the tab sending the {action}, from init or "
            "resume. One from before a resume elsewhere is refused with 409 "
            "capture_superseded. Omitted, the request is not checked."
        ),
    )


# Annotated, so a route called directly, as tests do, defaults to None.
ResumeEpoch = Annotated[
    Optional[int],
    Query(
        ge=0,
        le=INTEGER_MAX,
        description=(
            "The capture epoch of the tab that holds the capture, when that tab "
            "carries on with it. Refused with 409 capture_superseded once a "
            "resume elsewhere has issued a later one. Omitted, the caller takes "
            "the capture over and is issued the next epoch."
        ),
    ),
]

ReclaimEpoch = Annotated[
    Optional[int],
    Query(
        ge=0,
        le=INTEGER_MAX,
        description=(
            "With no epoch: the capture epoch the tab held before its page "
            "reloaded. A capture still uploading under that epoch is taken over "
            "at once, without waiting for it to stall. Any other value changes "
            "nothing."
        ),
    ),
]

SegmentSequence = Annotated[
    int,
    Query(ge=0, le=BIGINT_MAX, description="Sequence number of the segment"),
]

ResumeNextSequence = Annotated[
    Optional[int],
    Query(
        ge=0,
        le=BIGINT_MAX,
        description=(
            "The sequence the tab numbers its new audio from. Audio it recorded "
            "before the pause and has not uploaded yet arrives after the resume, "
            "so the line break goes at the larger of this and the sequence after "
            "the last segment the server holds."
        ),
    ),
]

SegmentResumeMark = Annotated[
    Optional[int],
    Query(
        ge=0,
        le=BIGINT_MAX,
        description=(
            "Sent with the first segment of new audio after a resume that "
            "restored audio recorded before the pause: the line break that resume "
            "stored (its last_sequence plus one), which moves to this segment."
        ),
    ),
]
