"""Search and filter conditions for the recordings list.

Each condition is a correlated EXISTS on ``Recording``, so a meeting with
several speakers or tags is tested once and listed once. Joining those tables
instead fanned each meeting out into a row per speaker and tag, evaluated the
transcript match on every one of those rows and needed a DISTINCT over every
recording column to fold them back: about four seconds a search over a
thousand meetings (#901).
"""

from collections.abc import Sequence

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Mapped
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col

from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.tag import RecordingTag, Tag
from backend.models.transcript import Transcript
from backend.services.person_meetings import counted_speaker
from backend.utils.canonical_pipeline.constants import ACTIVE_UTTERANCE_STATES

_ACTIVE_STATES = sorted(ACTIVE_UTTERANCE_STATES)

LIKE_ESCAPE = "\\"
# A phrase's words each have to be on some line before the joined transcript
# is built to look for it; a few of the longest are enough to rule most
# meetings out, and a long query does not grow the statement without bound.
PHRASE_PREFILTER_WORDS = 3


def search_term(query: str) -> str:
    """``query`` with each run of whitespace made one space, none at the ends.

    A settling capture's lines are joined with one space and the pipeline
    spaces a transcript's words singly, so a tab, a line break or two spaces
    between words would otherwise keep a phrase from ever matching.
    """
    return " ".join(query.split())


def like_pattern(term: str) -> str:
    """``term`` as a substring pattern, its own ``%`` and ``_`` taken literally."""
    escaped = (
        term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def _contains(
    column: ColumnElement[str]
    | ColumnElement[str | None]
    | Mapped[str]
    | Mapped[str | None],
    term: str,
) -> ColumnElement[bool]:
    return column.ilike(like_pattern(term), escape=LIKE_ESCAPE)


def _settling_marked() -> ColumnElement[bool]:
    """A capture whose canonical lines, not the snapshot, are its transcript."""
    state = col(Transcript.settling_state)
    return and_(
        state["eligible"].as_boolean().is_(True),
        state["policy"]["enabled"].as_boolean().is_(True),
    )


def _active_line_contains(term: str) -> ColumnElement[bool]:
    return exists(
        select(col(TranscriptUtterance.id))
        .where(col(TranscriptUtterance.recording_id) == col(Recording.id))
        .where(col(TranscriptUtterance.state).in_(_ACTIVE_STATES))
        .where(_contains(col(TranscriptUtterance.text), term))
        .correlate(Recording)
    )


def _joined_lines_contain(term: str) -> ColumnElement[bool]:
    """The lines joined in transcript order, so a phrase can cross a line break."""
    ordered = (
        select(col(TranscriptUtterance.text))
        .where(col(TranscriptUtterance.recording_id) == col(Recording.id))
        .where(col(TranscriptUtterance.state).in_(_ACTIVE_STATES))
        .order_by(col(TranscriptUtterance.sort_key), col(TranscriptUtterance.id))
        .correlate(Recording)
        .subquery()
    )
    # aggregate_strings emits string_agg on Postgres and group_concat on SQLite.
    joined = select(func.aggregate_strings(ordered.c.text, " ")).scalar_subquery()
    return _contains(func.coalesce(joined, ""), term)


def _canonical_text_contains(term: str) -> ColumnElement[bool]:
    """Whether a settling capture's lines hold ``term``.

    The lines are joined with a space, so a match that crosses from one line
    into the next takes in that space. A single word can only match within
    one line, which an EXISTS over the lines answers without building the
    joined text. A phrase is looked for in the joined text only when no
    single line holds it and each of its longest words is on a line.

    Whether to look across lines turns on the number of words, not the number
    of different words: "yes yes" can end one line and start the next. The
    prefilter takes each word once, in the order typed, so words of equal
    length are picked the same way in every process, which a set's order
    is not.
    """
    whole = _active_line_contains(term)
    words = term.split()
    if len(words) < 2:
        return whole
    longest = sorted(dict.fromkeys(words), key=len, reverse=True)
    prefilter = [
        _active_line_contains(word) for word in longest[:PHRASE_PREFILTER_WORDS]
    ]
    return or_(whole, and_(*prefilter, _joined_lines_contain(term)))


def _transcript_contains(term: str) -> ColumnElement[bool]:
    return exists(
        select(col(Transcript.id))
        .where(col(Transcript.recording_id) == col(Recording.id))
        .where(
            case(
                (_settling_marked(), _canonical_text_contains(term)),
                else_=_contains(col(Transcript.text), term),
            )
        )
        .correlate(Recording)
    )


def _speaker_name_contains(term: str) -> ColumnElement[bool]:
    """The name each speaker shows under, as the transcript and MCP read it."""
    shown_name = func.coalesce(
        func.nullif(col(RecordingSpeaker.local_name), ""),
        func.nullif(col(GlobalSpeaker.name), ""),
        func.nullif(col(RecordingSpeaker.name), ""),
        col(RecordingSpeaker.diarization_label),
    )
    return exists(
        select(col(RecordingSpeaker.id))
        .outerjoin(
            GlobalSpeaker,
            col(GlobalSpeaker.id) == col(RecordingSpeaker.global_speaker_id),
        )
        .where(col(RecordingSpeaker.recording_id) == col(Recording.id))
        .where(col(RecordingSpeaker.merged_into_id).is_(None))
        .where(_contains(shown_name, term))
        .correlate(Recording)
    )


def _tag_name_contains(term: str) -> ColumnElement[bool]:
    return exists(
        select(col(RecordingTag.id))
        .join(Tag, col(Tag.id) == col(RecordingTag.tag_id))
        .where(col(RecordingTag.recording_id) == col(Recording.id))
        .where(_contains(col(Tag.name), term))
        .correlate(Recording)
    )


def recording_search_condition(query: str) -> ColumnElement[bool] | None:
    """Meetings whose title, tags, speaker names or transcript hold ``query``.

    Case-insensitive substring matching of ``search_term(query)``. ``None``
    for a blank query. The cheap tests come first, as Postgres stops at the
    first that holds.
    """
    term = search_term(query)
    if not term:
        return None
    return or_(
        _contains(col(Recording.name), term),
        _tag_name_contains(term),
        _speaker_name_contains(term),
        _transcript_contains(term),
    )


def has_any_speaker(speaker_ids: Sequence[int]) -> ColumnElement[bool]:
    """Meetings that count for any of these People.

    A meeting counts through a speaker the People count reads
    (``counted_speaker``), so the filter lists the meetings Person Details
    counts: not one where all of a person's lines moved to someone else, nor
    one where their speaker was merged away. Ownership, archive and bin are
    the outer query's.
    """
    return exists(
        select(col(RecordingSpeaker.id))
        .where(col(RecordingSpeaker.recording_id) == col(Recording.id))
        .where(col(RecordingSpeaker.global_speaker_id).in_(list(speaker_ids)))
        .where(counted_speaker())
        .correlate(Recording)
    )


def has_any_tag(tag_ids: Sequence[int]) -> ColumnElement[bool]:
    """Meetings carrying any of these tags."""
    return exists(
        select(col(RecordingTag.id))
        .where(col(RecordingTag.recording_id) == col(Recording.id))
        .where(col(RecordingTag.tag_id).in_(list(tag_ids)))
        .correlate(Recording)
    )
