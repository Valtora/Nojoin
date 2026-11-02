"""The live lane passes a quarantined chunk at once, and reports a skip briefly (#808).

A chunk quarantined as ``.corrupt`` or ``.transcode_failed`` never arrives, so
the lane skips it as soon as a later chunk is on disk, as the chunk cursor does.
A chunk that is simply absent still waits for five later chunks.
"""

from __future__ import annotations

import pytest

from backend.processing import live_transcribe as lt
from backend.processing.live_gap import GapSkip, find_gap_skip
from backend.processing.live_persistence import LivePublicationResult
from backend.tests.test_live_transcription import (
    _gap_lane,
    _make_segment_wav,
    _run_live_task,
)


def _gap_metrics(monkeypatch) -> list[dict]:
    """The gap_skipped sequence outcomes the lane reports."""
    emitted: list[dict] = []
    report = lt._record_live_sequence_outcome_metric

    def capture(**kwargs):
        if kwargs.get("reason") == "gap_skipped":
            emitted.append(kwargs)
        return report(**kwargs)

    monkeypatch.setattr(lt, "_record_live_sequence_outcome_metric", capture)
    return emitted


@pytest.mark.parametrize("marker", [".corrupt", ".transcode_failed"])
def test_a_quarantined_head_is_skipped_at_once(monkeypatch, tmp_path, marker):
    temp_dir, _, transcript, session = _gap_lane(monkeypatch, tmp_path, 81, (1,))
    (temp_dir / f"0{marker}").write_text("undecodable\n")

    _run_live_task(monkeypatch, 81, 1, session)

    assert len(transcript.segments) == 1
    state = lt.read_live_state(temp_dir / "live")
    assert state["next_expected"] == 2
    assert state["sequence_outcomes"]["0"] == {
        "outcome": "skipped",
        "reason": "gap_skipped",
    }
    assert state["sequence_outcomes"]["1"]["outcome"] == "consumed"


def test_a_run_of_quarantined_chunks_is_skipped_together(monkeypatch, tmp_path):
    temp_dir, _, transcript, session = _gap_lane(monkeypatch, tmp_path, 82, (2,))
    (temp_dir / "0.corrupt").write_text("\n")
    (temp_dir / "1.transcode_failed").write_text("\n")

    _run_live_task(monkeypatch, 82, 2, session)

    assert len(transcript.segments) == 1
    state = lt.read_live_state(temp_dir / "live")
    assert state["next_expected"] == 3
    assert state["sequence_outcomes"]["1"]["reason"] == "gap_skipped"


def test_an_absent_chunk_after_a_quarantined_one_still_waits(monkeypatch, tmp_path):
    """0 is quarantined, 1 is still uploading: the lane waits for 1 as before."""
    temp_dir, audio_store, transcript, session = _gap_lane(
        monkeypatch, tmp_path, 83, (2, 3, 4, 5)
    )
    (temp_dir / "0.corrupt").write_text("\n")

    _run_live_task(monkeypatch, 83, 5, session)

    assert transcript.segments == []
    assert lt.read_live_state(temp_dir / "live")["next_expected"] == 0

    _make_segment_wav(temp_dir, 1, 2.0, audio_store)
    _run_live_task(monkeypatch, 83, 1, session)

    assert len(transcript.segments) == 1
    state = lt.read_live_state(temp_dir / "live")
    assert state["next_expected"] == 6
    assert state["sequence_outcomes"]["0"]["reason"] == "gap_skipped"
    assert state["sequence_outcomes"]["1"]["run"] == [1, 2, 3, 4, 5]


def test_a_chunk_on_disk_ends_the_skip_even_with_a_marker_beside_it(tmp_path):
    """A sequence quarantined once and then uploaded again is drained, not skipped."""
    (tmp_path / "0.corrupt").write_text("\n")
    (tmp_path / "1.corrupt").write_text("\n")
    (tmp_path / "1.wav").write_bytes(b"audio")
    (tmp_path / "2.wav").write_bytes(b"audio")

    assert find_gap_skip(tmp_path, 0) == GapSkip(0, 1)


def test_the_gap_metric_reports_a_range_not_every_sequence(monkeypatch, tmp_path):
    """A far-behind checkpoint skips thousands of sequences in one go."""
    emitted = _gap_metrics(monkeypatch)
    temp_dir, _, transcript, session = _gap_lane(
        monkeypatch, tmp_path, 84, range(3000, 3005)
    )

    _run_live_task(monkeypatch, 84, 3004, session)

    assert len(transcript.segments) == 1
    assert len(emitted) == 1
    payload = emitted[0]["extra_payload"]
    assert payload == {
        "skipped_first": 0,
        "skipped_last": 2999,
        "skipped_count": 3000,
        "next_expected": 3000,
    }
    outcomes = lt.read_live_state(temp_dir / "live")["sequence_outcomes"]
    assert len(outcomes) <= 200
    assert outcomes["2999"]["reason"] == "gap_skipped"


def test_a_skip_in_a_rejected_run_is_reported_once_it_is_saved(monkeypatch, tmp_path):
    """A run rejected by a pause or takeover saves nothing, so its skip is not
    reported; the run that next drains past the gap reports it, once."""
    emitted = _gap_metrics(monkeypatch)
    temp_dir, _, transcript, session = _gap_lane(monkeypatch, tmp_path, 85, (1, 2))
    (temp_dir / "0.corrupt").write_text("\n")
    persist = lt._persist_live_run
    calls = {"n": 0}

    def reject_first(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return LivePublicationResult(False)
        return persist(**kwargs)

    monkeypatch.setattr(lt, "_persist_live_run", reject_first)

    _run_live_task(monkeypatch, 85, 1, session)
    assert emitted == []
    assert lt.read_live_state(temp_dir / "live")["next_expected"] == 0

    _run_live_task(monkeypatch, 85, 2, session)
    assert len(emitted) == 1
    assert emitted[0]["extra_payload"]["skipped_count"] == 1
    assert lt.read_live_state(temp_dir / "live")["next_expected"] == 3

    # A redelivered task for an already drained chunk reports nothing more.
    _run_live_task(monkeypatch, 85, 1, session)
    assert len(emitted) == 1
    assert len(transcript.segments) == 1
