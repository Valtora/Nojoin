"""Sentence-start correction on the live lane (#813): native words only, through
publication, the label pass and repeated settling passes."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import Engine, event
from sqlmodel import Session, select

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.processing.live_diarization.evidence import SpeakerEvidence
from backend.processing.live_diarization.lines import LabelTimeline
from backend.processing.sentence_start import native_activity
from backend.tests.canonical_engine import (
    publication_engine as publication_engine,
)
from backend.tests.test_live_settling_pending import _enable_speakers
from backend.tests.test_live_settling_publication import (
    _request,
    _seed,
    settle_with_k,
)
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline.constants import LIVE_SPEAKER_LABEL_PAYLOAD_KEY
from backend.utils.canonical_pipeline.core import (
    serialize_canonical_utterances,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)
from backend.utils.canonical_pipeline.live_sentence_start import (
    LivePass,
    correct_sentence_starts,
)
from backend.utils.canonical_pipeline.live_settling import publish_live_settling
from backend.utils.canonical_pipeline.live_settling_projection import (
    SETTLING_KEY,
    line_is_locked,
    locked_context,
    locked_lines_between,
)

P1, P2, P3 = (f"LIVE_PROVISIONAL_{n}" for n in (1, 2, 3))
# "It is done. so um we go": speaker 1 ends a sentence, speaker 2 replies.
TOKENS = [
    (" It", 0, 300),
    (" is", 300, 600),
    (" done.", 600, 1000),
    (" so", 1050, 1200),
    (" um", 1200, 1300),
    (" we", 1300, 1500),
    (" go", 1500, 1800),
]
# "It is done. Yes. so um we go": speaker 3 agrees before speaker 2 replies.
AGREED = [
    *TOKENS[:3],
    (" Yes.", 1020, 1180),
    (" so", 1220, 1350),
    (" um", 1350, 1450),
    (" we", 1500, 1700),
    (" go", 1700, 1900),
]


def _posteriors() -> np.ndarray:
    """Speaker 1 to 1.3 s; speaker 2, quieter, from 1.0 s. "so" and "um" hold
    both, and speaker 1's larger share decides them."""
    probabilities = np.zeros((200, 8), np.float32)
    probabilities[0:130, 0] = 0.9
    probabilities[100:200, 1] = 0.6
    return probabilities


def _agreed_posteriors() -> np.ndarray:
    """Speaker 1 to 1.0 s, speaker 3 over "Yes.", then speaker 1 again to
    1.5 s with speaker 2, quieter, from 1.2 s: speaker 1 decides "so" and "um"."""
    probabilities = np.zeros((200, 8), np.float32)
    probabilities[0:100, 0] = 0.9
    probabilities[100:120, 2] = 0.9
    probabilities[120:150, 0] = 0.9
    probabilities[120:150, 1] = 0.6
    probabilities[150:200, 1] = 0.9
    return probabilities


def _evidence(**route: Any) -> SpeakerEvidence:
    empty = np.zeros(0)
    return SpeakerEvidence(
        LabelTimeline(empty, empty, empty.astype(np.int64), empty.astype(bool), 0.0),
        route.get("posteriors", _posteriors()),
        0,
        200,
        route.get("native", True),
        route.get("switch_frame"),
    )


def _words(
    count: int | None = None, tokens: list[tuple[str, int, int]] | None = None
) -> list[dict[str, Any]]:
    return [
        {"word": text, "start_ms": start, "end_ms": end}
        for text, start, end in (tokens or TOKENS)[:count]
    ]


def _speakers(session: Session, recording_id: int) -> list[tuple[str, str]]:
    return [
        (line["speaker"], line["text"])
        for line in serialize_canonical_utterances(session, recording_id)
    ]


# --- through publication and the label pass (PostgreSQL) -------------------------
def test_a_published_reply_start_moves_to_the_replying_speaker(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    evidence = _evidence()
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)
        publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(),
            decide_speaker=evidence.decide,
            activity=evidence.native_activity(),
        )
        session.commit()

        assert _speakers(session, recording_id) == [
            (P1, "It is done."),
            (P2, "so um we go"),
        ]


def test_the_label_pass_moves_a_reply_start_publication_left(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    evidence = _evidence()
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)
        publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(),
            decide_speaker=evidence.decide,
        )
        session.commit()
        assert _speakers(session, recording_id)[0] == (P1, "It is done. so um")

        apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=0,
            decide=evidence.decide,
            activity=evidence.native_activity(),
        )
        session.commit()

        assert _speakers(session, recording_id) == [
            (P1, "It is done."),
            (P2, "so um we go"),
        ]


def test_a_tail_word_locked_after_its_move_lets_the_rest_follow_on_later_passes(
    settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With one unchanged comparison to lock, "so" locks a pass before "um".
    "so" is decided again for the rule's input; read back as the speaker 2 the
    rule gave it, it would split the tail and send "um" back to speaker 1."""
    settle_with_k(monkeypatch, 1)
    recording_id = _seed(settling_engine)
    evidence = _evidence()
    passes = [_words(4), _words(), _words()]
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)
        for step, words in enumerate(passes):
            publish_live_settling(
                session,
                request=_request(session, recording_id, step),
                words=words,
                decide_speaker=evidence.decide,
                activity=evidence.native_activity(),
            )
            session.commit()

        lines = serialize_canonical_utterances(session, recording_id)
        so = next(w for line in lines for w in line["words"] if w["word"] == " so")
        assert so["locked"]
        assert _speakers(session, recording_id) == [
            (P1, "It is done."),
            (P2, "so um we go"),
        ]


def test_the_label_pass_keeps_a_tail_whose_sentence_end_has_locked(
    settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the second publication "It is done." is a locked line and "so" is
    locked on speaker 2, while "um" is not. The label pass reads only open
    lines; it must still see the sentence end, or "um" goes back to speaker 1
    between two speaker 2 words."""
    settle_with_k(monkeypatch, 1)
    recording_id = _seed(settling_engine)
    evidence = _evidence()
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)
        for step, words in enumerate([_words(4), _words()]):
            publish_live_settling(
                session,
                request=_request(session, recording_id, step),
                words=words,
                decide_speaker=evidence.decide,
                activity=evidence.native_activity(),
            )
            session.commit()
        lines = serialize_canonical_utterances(session, recording_id)
        assert lines[0]["locked"] and not lines[1]["locked"]

        apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=0,
            decide=evidence.decide,
            activity=evidence.native_activity(),
        )
        session.commit()

        assert _speakers(session, recording_id) == [
            (P1, "It is done."),
            (P2, "so um we go"),
        ]


def test_a_tail_word_locked_on_the_previous_speaker_keeps_the_whole_tail(
    settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "so" locks on speaker 1 (its second unchanged reading) before the reply
    arrives; moving only "um" would leave speaker 1, 2, 1, 2."""
    settle_with_k(monkeypatch, 1)
    recording_id = _seed(settling_engine)
    evidence = _evidence()
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)
        for step, words in enumerate([_words(4), _words(4), _words()]):
            publish_live_settling(
                session,
                request=_request(session, recording_id, step),
                words=words,
                decide_speaker=evidence.decide,
                activity=evidence.native_activity(),
            )
            session.commit()

        assert _speakers(session, recording_id) == [
            (P1, "It is done. so um"),
            (P2, "we go"),
        ]


# --- a line a person locked among the open lines (SQLite and PostgreSQL) -----------
def _word_speakers(session: Session, recording_id: int) -> dict[str, str]:
    """Per word: the label pass regroups the open words round an edited line."""
    return {
        word["word"].strip(): line["speaker"]
        for line in serialize_canonical_utterances(session, recording_id)
        for word in line["words"]
    }


def _agreed_then_edited(session: Session, recording_id: int) -> SpeakerEvidence:
    """Publish "It is done. Yes. so um we go", then edit "Yes." as a person
    does mid-meeting: the lines either side of it are still open."""
    evidence = _evidence(posteriors=_agreed_posteriors())
    _enable_speakers(session, recording_id)
    publish_live_settling(
        session,
        request=_request(session, recording_id, 0),
        words=_words(tokens=AGREED),
        decide_speaker=evidence.decide,
        activity=evidence.native_activity(),
    )
    session.commit()
    # "Yes." is speaker 3's, so "so um" is speaker 1's whole run and stays.
    assert _speakers(session, recording_id) == [
        (P1, "It is done."),
        (P3, "Yes."),
        (P1, "so um"),
        (P2, "we go"),
    ]
    yes = serialize_canonical_utterances(session, recording_id)[1]
    update_utterance_text(
        session,
        recording_id=recording_id,
        utterance_public_id=yes["id"],
        text="Yes, agreed.",
    )
    session.commit()
    return evidence


def test_the_label_pass_reads_a_line_a_person_edited_between_open_lines(
    publication_engine: Engine,
) -> None:
    """The edit locks "Yes." while "It is done." and "so um" are open, so the
    label pass's read leaves it out. Without it "done." ends the sentence
    "so um" follows, and they go to speaker 2 until publication, which reads
    the edited line, gives them back."""
    recording_id = _seed(publication_engine)
    with Session(publication_engine) as session:
        evidence = _agreed_then_edited(session, recording_id)

        apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=0,
            decide=evidence.decide,
            activity=evidence.native_activity(),
        )
        session.commit()

        assert (P3, "Yes, agreed.") in _speakers(session, recording_id)
        speakers = _word_speakers(session, recording_id)
        assert [speakers[word] for word in ("so", "um", "we", "go")] == [P1, P1, P2, P2]


def test_the_label_pass_reads_an_edited_line_its_one_open_line_spans(
    publication_engine: Engine,
) -> None:
    """The label step also runs when a settling step was skipped, so it reads
    lines it grouped itself. While speaker 2 is unheard it leaves one open line
    holding the words either side of "Yes."; the next pass hears speaker 2 and
    must still find the edited line among that line's words."""
    recording_id = _seed(publication_engine)
    with Session(publication_engine) as session:
        evidence = _agreed_then_edited(session, recording_id)
        apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=0,
            decide=lambda _start, _end: LiveLabelDecision(P1, False, 1.0),
            activity=evidence.native_activity(),
        )
        session.commit()
        assert _speakers(session, recording_id) == [
            (P1, "It is done. so um we go"),
            (P3, "Yes, agreed."),
        ]

        apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=0,
            decide=evidence.decide,
            activity=evidence.native_activity(),
        )
        session.commit()

        speakers = _word_speakers(session, recording_id)
        assert [speakers[word] for word in ("so", "um", "we", "go")] == [P1, P1, P2, P2]


def _stored(
    session: Session, recording_id: int, starts: list[int], *, locked: bool = False
) -> TranscriptUtterance:
    """A stored live line with one word at each start, open unless ``locked``."""
    words: list[dict[str, Any]] = [
        {"word": f" w{start}", "start_ms": start, "end_ms": start + 80}
        for start in starts
    ]
    row = TranscriptUtterance(
        recording_id=recording_id,
        public_id=str(uuid4()),
        sort_key=f"{starts[0]:020d}",
        start_ms=starts[0],
        end_ms=starts[-1] + 80,
        text="".join(word["word"] for word in words).strip(),
        state=TranscriptUtteranceState.PROVISIONAL,
        source_kind="live",
        speaker_assignment_source="live",
        speaker_assignment_authority="provisional",
        confidence_payload={
            "asr_segments": [{"words": words}],
            SETTLING_KEY: {"version": 1, "line_locked": locked},
        },
    )
    session.add(row)
    session.flush()
    return row


def _locked_ids(session: Session, recording_id: int) -> frozenset[int]:
    """What ``locked_line_ids`` returns on PostgreSQL, read here on either."""
    lines = session.exec(
        select(TranscriptUtterance).where(
            TranscriptUtterance.recording_id == recording_id
        )
    )
    return frozenset(
        line.id for line in lines if line.id is not None and line_is_locked(line)
    )


def _kept_and_plain(
    session: Session, recording_id: int, rows: list[TranscriptUtterance]
) -> list[int]:
    """The starts ``locked_lines_between`` selects, the same on kept flags
    (PostgreSQL's way) as on each line's own payload."""
    locked = _locked_ids(session, recording_id)
    kept = locked_lines_between(session, recording_id, rows, locked)
    plain = locked_lines_between(session, recording_id, rows, None)
    assert [line.id for line in kept] == [line.id for line in plain]
    return [line.start_ms for line in kept]


def test_only_the_newest_locked_line_between_two_open_words_is_read(
    publication_engine: Engine,
) -> None:
    """An open line from early in a meeting must not have every label step
    read the payload of every line locked since (#776, #880)."""
    recording_id = _seed(publication_engine)
    with Session(publication_engine) as session:
        early = _stored(session, recording_id, [0, 100])
        for start in range(1000, 31000, 1000):
            _stored(session, recording_id, [start], locked=True)
        tail = _stored(session, recording_id, [40000, 40500])
        _stored(session, recording_id, [40200], locked=True)
        _stored(session, recording_id, [40300], locked=True)
        _stored(session, recording_id, [50000], locked=True)
        session.commit()

        assert _kept_and_plain(session, recording_id, [early, tail]) == [30000, 40300]
        # An open word between two locked lines keeps both: each bounds it.
        between = _stored(session, recording_id, [15500])
        session.commit()
        rows = [early, between, tail]
        assert _kept_and_plain(session, recording_id, rows) == [15000, 30000, 40300]

        # On kept flags only the three lines kept are read whole; the open
        # lines are in hand, and nothing else is in the session to reuse.
        locked = _locked_ids(session, recording_id)
        session.expunge_all()
        read: list[int] = []

        def record(line: TranscriptUtterance, _context: Any) -> None:
            read.append(line.start_ms)

        event.listen(TranscriptUtterance, "load", record)
        try:
            locked_lines_between(session, recording_id, rows, locked)
        finally:
            event.remove(TranscriptUtterance, "load", record)
        assert sorted(read) == [15000, 30000, 40300]


def test_a_locked_line_before_the_open_lines_is_read_once(
    publication_engine: Engine,
) -> None:
    recording_id = _seed(publication_engine)
    with Session(publication_engine) as session:
        _stored(session, recording_id, [0], locked=True)
        before = _stored(session, recording_id, [1000], locked=True)
        first = _stored(session, recording_id, [1000, 3000])
        among = _stored(session, recording_id, [2000], locked=True)
        last = _stored(session, recording_id, [4000])
        _stored(session, recording_id, [4500], locked=True)
        session.commit()

        context = locked_context(session, recording_id, [first, last])

        assert [line.id for line in context] == [before.id, among.id]
        assert locked_context(session, recording_id, []) == ()
        assert locked_context(session, recording_id, [last]) == (among,)


# --- one pass, in memory -----------------------------------------------------------
def _row(row_id: int, words: list[dict[str, Any]], speaker: str, **flags: Any):
    """A provisional live row as the settling lane stores it."""
    return TranscriptUtterance(
        id=row_id,
        recording_id=1,
        start_ms=words[0]["start_ms"],
        end_ms=words[-1]["end_ms"],
        speaker_label=speaker,
        state=TranscriptUtteranceState.PROVISIONAL,
        source_kind="live",
        text="".join(word["word"] for word in words),
        sort_key=row_id,
        manual_speaker_locked=flags.get("manual", False),
        confidence_payload={
            "asr_segments": [{"words": words}],
            "live_text_settling": {"version": 1, "line_locked": flags.get("locked")},
            LIVE_SPEAKER_LABEL_PAYLOAD_KEY: {
                "locked": bool(flags.get("locked")),
                "locked_word_ids": [],
            },
        },
    )


def _one_pass(
    rows: list[TranscriptUtterance],
    words: list[dict[str, Any]],
    *,
    evidence: SpeakerEvidence | None = None,
    resume: tuple[int, ...] = (),
) -> list[str]:
    evidence = evidence or _evidence()
    activity = evidence.native_activity()
    assert activity is not None
    decisions = [
        evidence.decide(w["start_ms"], max(w["start_ms"] + 1, w["end_ms"]))
        for w in words
    ]
    corrected = correct_sentence_starts(
        words, rows, decisions, evidence.decide, LivePass(activity, resume, 1.0)
    )
    return [decision.speaker for decision in corrected]


def _locked_line_then_open_tail() -> tuple[list[TranscriptUtterance], list[dict]]:
    words = [dict(word, id=f"w{i}", locked=i < 3) for i, word in enumerate(_words())]
    rows = [
        _row(1, words[:3], P1, locked=True),
        _row(2, words[3:], P1),
    ]
    return rows, words[3:]


def test_a_sentence_ended_in_a_locked_line_still_starts_the_tail():
    """The line holding "done." has locked and left the pass's words; it is
    read as context, so "so um" still moves."""
    rows, open_words = _locked_line_then_open_tail()

    assert _one_pass(rows, open_words) == [P2, P2, P2, P2]


def test_no_tail_follows_a_locked_line_across_a_resume_cut():
    rows, open_words = _locked_line_then_open_tail()

    assert _one_pass(rows, open_words, resume=(1020,)) == [P1, P1, P2, P2]


def test_a_line_a_person_gave_another_speaker_is_not_the_previous_turn():
    """The user moved "It is done." to speaker 3: speaker 1 has no turn there
    to keep, so "so um" is speaker 1's whole run and stays."""
    rows, open_words = _locked_line_then_open_tail()
    rows[0] = _row(
        1,
        rows[0].confidence_payload["asr_segments"][0]["words"],
        P3,
        manual=True,
    )

    assert _one_pass(rows, open_words) == [P1, P1, P2, P2]


@pytest.mark.parametrize("we_end_ms", [1500, 1300])
def test_words_decided_by_clustering_after_a_hand_back_are_left_alone(we_end_ms):
    """Native output ends at 1.3 s, where clustering takes over: "so um" were
    decided natively, but "we", the next speaker's first word, by clustering,
    zero-length or not (its speaker is read over at least 1 ms)."""
    words = [dict(word, id=f"w{i}") for i, word in enumerate(_words())]
    words[5]["end_ms"] = we_end_ms
    rows = [_row(1, words, P1)]
    switched = SpeakerEvidence(
        LabelTimeline(
            np.array([0.0, 1.3]),
            np.array([1.3, 1.8]),
            np.array([0, 1]),
            np.array([True, True]),
            2.0,
        ),
        _posteriors()[:130],
        0,
        130,
        False,
        0,
    )

    assert _one_pass(rows, words, evidence=switched)[3:5] == [P1, P1]
    assert _one_pass(rows, words)[3:5] == [P2, P2]


def test_a_classic_capture_has_no_native_activity():
    assert native_activity(_posteriors(), 0, 200, native=False, switched=False) is None


def test_a_zero_length_live_word_reads_the_next_speaker_at_its_instant():
    words = [dict(word, id=f"w{i}") for i, word in enumerate(_words())]
    words[3] = dict(words[3], start_ms=1100, end_ms=1100)
    rows = [_row(1, words, P1)]
    evidence = _evidence()
    activity = evidence.native_activity()
    assert activity is not None
    decisions = [
        evidence.decide(w["start_ms"], max(w["start_ms"] + 1, w["end_ms"]))
        for w in words
    ]
    assert decisions[3].speaker == P1

    result = correct_sentence_starts(
        words, rows, decisions, evidence.decide, LivePass(activity, (), 1.0)
    )

    assert [decision.speaker for decision in result[3:5]] == [P2, P2]
