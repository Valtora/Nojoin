"""Live picks and merges reach the processed meeting (#775).

The live lines are ``live_naming_fixtures.LIVE_LINES``: ``ONE`` speaks at 0-6 s
and 9.5-20 s, ``TWO`` at 6-9 s and 21-22 s, ``THREE`` at 30-33 s and 34-36 s.
The final pass then runs ``assign_and_identify_speakers`` and
``finalize_utterances_from_segments`` on its own labels, as the worker does.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlmodel import select

from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording
from backend.tests.live_naming_fixtures import ONE, THREE, TWO
from backend.tests.test_live_speaker_naming_carryover import FINAL, _name_live
from backend.tests.test_live_speaker_naming_carryover import session as session
from backend.utils.canonical_pipeline import finalize_utterances_from_segments
from backend.utils.speaker_names.live import (
    live_speaker_names,
    record_live_speaker_merge,
)
from backend.worker.tasks import speaker_assignment


def _pick(db, start_s: float, label: str) -> None:
    """Move the live line starting at ``start_s`` to live label ``label``.

    A line the user moved to a live speaker keeps a manual speaker naming that
    live label, with no recording speaker behind it.
    """
    line = db.exec(
        select(TranscriptUtterance).where(
            TranscriptUtterance.start_ms == int(start_s * 1000)
        )
    ).one()
    line.speaker_label = label
    line.recording_speaker_id = None
    line.manual_speaker_locked = True
    line.speaker_assignment_source = "manual"
    db.add(line)
    db.commit()


def _merge(db, label: str, into: str) -> None:
    assert record_live_speaker_merge(db, 1, label, into, actor_user_id=1)
    db.commit()


def _process(db, monkeypatch, final=FINAL) -> None:
    monkeypatch.setattr(
        speaker_assignment, "_extract_speaker_voiceprints", lambda *args: {}
    )
    monkeypatch.setattr(
        speaker_assignment, "_run_duplicate_merge_pass", lambda *args, **kwargs: None
    )
    db.execute(text("UPDATE recordings SET status = 'PROCESSING'"))
    db.commit()
    segments = [
        {"start": start, "end": end, "speaker": label, "text": f"final {start}"}
        for label, start, end in final
    ]
    ctx = type("Ctx", (), {"session": db, "merged_config": {}, "recording_id": 1})()
    speaker_assignment.assign_and_identify_speakers(
        ctx, db.get(Recording, 1), segments, None
    )
    finalize_utterances_from_segments(
        db,
        recording_id=1,
        segments=[dict(s) for s in segments],
        trigger_source="worker",
    )
    db.commit()


def _final_lines(db) -> dict[float, dict[str, Any]]:
    """Each final line's start (s) -> the speaker it shows, followed through merges."""
    rows = db.execute(
        text(
            "SELECT u.start_ms, s.id, s.diarization_label, s.local_name, s.merged_into_id, "
            "s.identity_locked, u.manual_speaker_locked FROM transcript_utterances u "
            "LEFT JOIN recording_speakers s ON s.id = u.recording_speaker_id "
            "WHERE UPPER(u.state) = 'FINALIZED' ORDER BY u.start_ms"
        )
    ).all()
    speakers = {
        row[0]: row
        for row in db.execute(
            text(
                "SELECT id, diarization_label, local_name, merged_into_id FROM recording_speakers"
            )
        ).all()
    }
    lines = {}
    for start_ms, speaker_id, label, name, merged_into, locked, manual in rows:
        while merged_into is not None:
            speaker_id, label, name, merged_into = speakers[merged_into]
        lines[start_ms / 1000] = {
            "speaker": label,
            "name": name,
            "manual": bool(manual),
        }
    return lines


def _shown(db) -> dict[str, dict[str, Any]]:
    """The speakers the processed meeting lists: not merged away."""
    rows = db.execute(
        text(
            "SELECT diarization_label, local_name, identity_locked FROM recording_speakers "
            "WHERE merged_into_id IS NULL"
        )
    ).all()
    return {row[0]: {"name": row[1], "locked": bool(row[2])} for row in rows}


def test_a_line_moved_to_a_live_speaker_ends_on_that_speakers_final_speaker(
    session, monkeypatch
) -> None:
    # The user heard ONE at 6-9 s, where the live diariser had TWO.
    _pick(session, 6.0, ONE)

    # The final pass has 6-9 s as SPEAKER_00, TWO's voice, but nudged off the
    # live line's bounds, so it inherits as a correction near a boundary does.
    final = [*FINAL[:1], ("SPEAKER_00", 6.1, 8.9), *FINAL[2:]]
    _process(session, monkeypatch, final)

    lines = _final_lines(session)
    assert lines[6.1]["speaker"] == "SPEAKER_01"
    assert lines[6.1]["manual"] is True
    # Nothing else moved: TWO's own line stays with the final pass's speaker.
    assert lines[21.0]["speaker"] == "SPEAKER_00"
    assert lines[0.0]["speaker"] == "SPEAKER_01"


def test_a_picked_line_follows_a_name_given_after_the_pick(
    session, monkeypatch
) -> None:
    _pick(session, 6.0, ONE)
    _name_live(session, ONE, "Alex Example")

    _process(session, monkeypatch)

    lines = _final_lines(session)
    # The final segment matching the live line exactly keeps its manual speaker.
    assert lines[6.0] == {
        "speaker": "SPEAKER_01",
        "name": "Alex Example",
        "manual": True,
    }
    assert _shown(session)["SPEAKER_01"] == {"name": "Alex Example", "locked": True}


def test_a_merged_pair_ends_as_one_final_speaker(session, monkeypatch) -> None:
    # The final pass gave THREE's voice its own speaker, SPEAKER_02.
    _merge(session, THREE, ONE)

    _process(session, monkeypatch)

    shown = _shown(session)
    assert "SPEAKER_02" not in shown
    lines = _final_lines(session)
    assert {lines[30.0]["speaker"], lines[0.0]["speaker"]} == {"SPEAKER_01"}
    # The fold merged no one else: TWO's voice is still its own speaker.
    assert lines[6.0]["speaker"] == "SPEAKER_00"
    assert set(shown) == {"SPEAKER_00", "SPEAKER_01"}


def test_a_merge_never_folds_a_final_speaker_another_live_speaker_holds(
    session, monkeypatch
) -> None:
    # The final pass put THREE's speech with TWO's voice, in SPEAKER_00.
    final = [*FINAL[:4], ("SPEAKER_00", 30.0, 36.0)]
    _merge(session, THREE, ONE)

    _process(session, monkeypatch, final)

    lines = _final_lines(session)
    # Only the final line THREE's live lines cover moves to ONE's speaker.
    assert lines[30.0]["speaker"] == "SPEAKER_01"
    assert lines[6.0]["speaker"] == "SPEAKER_00"
    assert lines[21.0]["speaker"] == "SPEAKER_00"
    assert set(_shown(session)) == {"SPEAKER_00", "SPEAKER_01"}


def test_a_merge_then_a_rename_names_the_whole_merged_speaker(
    session, monkeypatch
) -> None:
    _name_live(session, THREE, "Bea Sample")
    _merge(session, THREE, ONE)
    # Named on THREE's label after the merge: that is the merged speaker, ONE.
    _name_live(session, THREE, "Casey Example")

    assert live_speaker_names(session, 1) == {
        ONE: "Casey Example",
        THREE: "Casey Example",
    }
    _process(session, monkeypatch)

    shown = _shown(session)
    assert shown["SPEAKER_01"] == {"name": "Casey Example", "locked": True}
    assert "SPEAKER_02" not in shown
    # The merge superseded THREE's own name, so it lands nowhere.
    assert all(speaker["name"] != "Bea Sample" for speaker in shown.values())
    assert _final_lines(session)[30.0]["name"] == "Casey Example"


def test_an_unclear_live_speaker_still_takes_its_picked_lines_where_its_name_goes(
    session, monkeypatch
) -> None:
    # TWO's own speech (6-9 s, 21-22 s) is split 2 s : 2 s between SPEAKER_00
    # and SPEAKER_03, so no final speaker clearly holds it.
    final = [
        ("SPEAKER_01", 0.0, 6.0),
        ("SPEAKER_00", 6.0, 8.0),
        ("SPEAKER_03", 8.0, 9.0),
        ("SPEAKER_01", 9.5, 20.0),
        ("SPEAKER_03", 21.0, 22.0),
        ("SPEAKER_02", 30.0, 33.0),
        ("SPEAKER_02", 34.0, 36.0),
    ]
    _name_live(session, TWO, "Blair Sample")
    _pick(session, 30.0, TWO)

    _process(session, monkeypatch, final)

    shown = _shown(session)
    # The name is offered on the top holder, as "Blair Sample?", and the picked
    # line goes to that same speaker. No final speakers were merged.
    assert shown["SPEAKER_00"] == {"name": "Blair Sample", "locked": False}
    assert set(shown) == {"SPEAKER_00", "SPEAKER_01", "SPEAKER_02", "SPEAKER_03"}
    lines = _final_lines(session)
    assert lines[30.0]["speaker"] == "SPEAKER_00"
    assert lines[34.0]["speaker"] == "SPEAKER_02"


def test_a_pick_with_no_final_speaker_to_go_to_leaves_no_live_label(
    session, monkeypatch
) -> None:
    # Every line THREE had is moved elsewhere, then a line is moved to THREE:
    # THREE has no speech of its own left for the final pass to place.
    _pick(session, 30.0, ONE)
    _pick(session, 34.0, ONE)
    _pick(session, 21.0, THREE)

    _process(session, monkeypatch)

    lines = _final_lines(session)
    assert lines[21.0]["speaker"] == "SPEAKER_00"
    # The final 30-36 s line covers both lines moved to ONE.
    assert lines[30.0]["speaker"] == "SPEAKER_01"
    labels = {
        row[0]
        for row in session.execute(
            text(
                "SELECT speaker_label FROM transcript_utterances "
                "WHERE UPPER(state) = 'FINALIZED'"
            )
        ).all()
    }
    assert not any(str(label).startswith("LIVE_") for label in labels)


def test_a_merged_label_reads_as_the_label_it_joined(session) -> None:
    _merge(session, THREE, ONE)
    # ONE has no name yet, so THREE's lines read as "Speaker 1".
    assert live_speaker_names(session, 1) == {THREE: "Speaker 1"}
    _merge(session, ONE, TWO)
    # Merging ONE moves its whole group, THREE included.
    from backend.utils.speaker_names.live import live_speaker_groups

    assert live_speaker_groups(session, 1) == {THREE: TWO, ONE: TWO}
    assert live_speaker_names(session, 1) == {THREE: "Speaker 2", ONE: "Speaker 2"}
