"""Query parameters the capture lifecycle routes share."""

from typing import Annotated, Optional

from fastapi import Query


def capture_epoch_query(action: str):
    return Query(
        None,
        ge=0,
        description=(
            f"The capture epoch of the tab sending the {action}, from init or "
            "resume. One from before a resume elsewhere is refused with 409 "
            "capture_superseded. Omitted, the request is not checked."
        ),
    )


# Annotated, so a route called directly, as tests do, defaults to None.
ResumeNextSequence = Annotated[
    Optional[int],
    Query(
        ge=0,
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
        description=(
            "Sent with the first segment of new audio after a resume that "
            "restored audio recorded before the pause: the line break that resume "
            "stored (its last_sequence plus one), which moves to this segment."
        ),
    ),
]
