"""One clustering step and one re-cluster pass, on hand-built inputs."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from backend.processing.live_diarization import recluster as recluster_module
from backend.processing.live_diarization.clustering import (
    ClusteringParams,
    OnlineSpeakerClustering,
)
from backend.processing.live_diarization.engine import (
    LiveLabelConfig,
    LiveLabelEngine,
    Piece,
)
from backend.processing.live_diarization.lines import LabelTimeline
from backend.processing.live_diarization.recluster import (
    ReclusterParams,
    UnitBank,
    UnitTable,
    _units_in_pass,
    recluster,
)

PARAMS = LiveLabelConfig().clustering
FRAMES = 100
DIM = 8


def _unit(*values: float) -> np.ndarray:
    vector = np.array(values + (0.0,) * (DIM - len(values)), np.float32)
    return vector / np.linalg.norm(vector)


def _segmentation(*shares: float) -> np.ndarray:
    """Local speaker k active on its first ``shares[k]`` of the frames."""
    seg = np.zeros((FRAMES, len(shares)), np.float32)
    for local, share in enumerate(shares):
        seg[: int(share * FRAMES), local] = 1.0
    return seg


def _clustering_with(*centres: np.ndarray, params: ClusteringParams = PARAMS):
    clustering = OnlineSpeakerClustering(params)
    clustering.identify(_segmentation(*([0.9] * len(centres))), np.stack(centres))
    return clustering


A, B, C = _unit(1), _unit(0, 1), _unit(0, 0, 1)


def test_the_first_step_founds_a_speaker_for_every_active_voice():
    clustering = OnlineSpeakerClustering(PARAMS)
    step = clustering.identify(_segmentation(0.9, 0.1, 0.0), np.stack([A, B, C]))

    assert step.pairs == [(0, 0), (1, 1)]
    assert step.founded == 2


def test_a_close_voice_joins_and_updates_its_speaker():
    clustering = _clustering_with(A, B)
    near_a = _unit(1, 0.3)  # cosine distance about 0.04
    step = clustering.identify(_segmentation(0.9), near_a[None])

    assert step.pairs == [(0, 0)]
    assert np.allclose(clustering.centers[0], A + near_a)


def test_a_short_close_voice_joins_without_moving_the_centroid():
    clustering = _clustering_with(A)
    step = clustering.identify(_segmentation(0.3), _unit(1, 0.3)[None])

    assert step.pairs == [(0, 0)]
    assert np.allclose(clustering.centers[0], A)


def test_a_long_new_voice_founds_a_speaker_and_a_short_one_is_held():
    clustering = _clustering_with(A, B)
    step = clustering.identify(
        _segmentation(0.9, 0.3), np.stack([C, _unit(1, 1, 1, 1, 9)])
    )

    assert step.pairs == [(0, 2)]
    assert step.extra == [(1, clustering.held_slot)]
    assert clustering.active[:3].tolist() == [True, True, True]


def test_a_split_voice_shares_its_sibling_label_instead_of_a_new_one():
    # The segmentation split speaker A in two: the one-to-one assignment gives
    # A's centroid to one half, and the other half is within the split guard.
    clustering = _clustering_with(A, B)
    halves = np.stack([_unit(1, 0.1), _unit(1, 0, 0.9)])  # 0.005 and 0.26 from A
    before = clustering.centers[0].copy()
    step = clustering.identify(_segmentation(0.9, 0.9), halves)

    assert step.pairs == [(0, 0)]
    assert step.extra == [(1, 0)]
    assert step.founded == 0
    assert np.allclose(clustering.centers[0], before + halves[0])


def test_beyond_the_split_guard_a_long_voice_is_new():
    clustering = _clustering_with(A, B)
    step = clustering.identify(
        _segmentation(0.9, 0.9),
        np.stack([A, _unit(1, 0, 3)]),  # 0.68 from A
    )

    assert step.pairs == [(0, 0), (1, 2)]
    assert step.extra == []


def test_with_every_slot_taken_a_new_voice_is_held():
    small = ClusteringParams(**{**PARAMS.__dict__, "max_speakers": 3})
    clustering = _clustering_with(A, B, params=small)
    step = clustering.identify(_segmentation(0.9), C[None])

    assert step.pairs == []
    assert step.extra == [(0, clustering.held_slot)]


def test_the_last_slot_below_the_held_one_still_founds_a_speaker():
    # Slots 0-2 can be founded; slot 3 is the held slot and is never one.
    small = ClusteringParams(**{**PARAMS.__dict__, "max_speakers": 4})
    clustering = _clustering_with(A, B, params=small)
    step = clustering.identify(_segmentation(0.9), C[None])

    assert step.pairs == [(0, 2)]
    assert step.founded == 1
    assert clustering.active.tolist() == [True, True, True, False]


def test_a_held_unit_at_the_slot_cap_ages_into_a_line_that_settles(tmp_path):
    # Every slot below the held one is founded, so the re-cluster cannot name
    # the held voice's cluster. Its unit keeps the held slot, locks with age,
    # and the line holding it settles unlabelled.
    params = ClusteringParams(**{**PARAMS.__dict__, "max_speakers": 3})
    clustering = _clustering_with(A, B, params=params)
    table, bank = _table(tmp_path, [(A, 0, True), (B, 1, True), (C, 2, False)], held=2)

    recluster(table, bank, clustering, RECLUSTER, t_now=10.0)
    assert table.label == [0, 1, 2]
    assert table.locked == [True, True, False]
    recluster(table, bank, clustering, RECLUSTER, t_now=RECLUSTER.lock_age_s)
    assert table.label == [0, 1, 2]
    assert table.locked == [True, True, True]

    engine = LiveLabelEngine(LiveLabelConfig(clustering=params), bank)
    engine.units = table
    engine.clustering = clustering
    engine.next_end_n = 20 * engine.config.sample_rate
    engine.pieces = [Piece(start=1.0, end=4.0, unit=2)]
    decision = LabelTimeline.from_engine(engine).label_line(1.0, 4.0)
    assert decision.speaker == "UNKNOWN"
    assert decision.locked is True


RECLUSTER = ReclusterParams(
    every_s=10.0,
    threshold=0.45,
    min_units=1,
    max_steps=10,
    lock_passes=2,
    lock_age_s=600.0,
    recent_s=1200.0,
    max_units=1000,
    anchors=10,
)


def _table(tmp_path, units: list[tuple[np.ndarray, int, bool]], held: int):
    """A unit table and bank holding closed units (vector, label, locked)."""
    bank = UnitBank(tmp_path / "bank", DIM)
    table = UnitTable(held + 1, DIM)
    for vector, label, locked in units:
        table.label.append(label)
        table.t0.append(0.0)
        table.stable.append(0)
        table.locked.append(locked)
        table.steps.append(4)
        table.long.append(1)
        table.row.append(bank.append(vector))
    return table, bank


def test_a_recluster_moves_a_split_off_unit_to_the_speaker_holding_most_speech(
    tmp_path,
):
    params = ClusteringParams(**{**PARAMS.__dict__, "max_speakers": 4})
    clustering = _clustering_with(A, B, params=params)
    table, bank = _table(
        tmp_path,
        [(A, 0, False), (A, 0, False), (_unit(1, 0.05), 1, False), (C, 2, False)],
        held=3,
    )
    result = recluster(table, bank, clustering, RECLUSTER, t_now=10.0)

    assert table.label == [0, 0, 0, 2]
    assert result.relabelled == 1
    assert clustering.active[:3].tolist() == [True, False, True]


def test_a_locked_unit_keeps_its_label_through_a_recluster(tmp_path):
    params = ClusteringParams(**{**PARAMS.__dict__, "max_speakers": 4})
    clustering = _clustering_with(A, B, params=params)
    table, bank = _table(
        tmp_path, [(A, 0, False), (A, 0, False), (_unit(1, 0.05), 1, True)], held=3
    )
    recluster(table, bank, clustering, RECLUSTER, t_now=10.0)

    assert table.label == [0, 0, 1]


def test_a_held_unit_takes_its_first_label_at_the_recluster(tmp_path):
    params = ClusteringParams(**{**PARAMS.__dict__, "max_speakers": 4})
    clustering = _clustering_with(A, B, params=params)
    table, bank = _table(
        tmp_path, [(A, 0, False), (B, 1, False), (_unit(0.1, 1), 3, False)], held=3
    )
    result = recluster(table, bank, clustering, RECLUSTER, t_now=10.0)

    assert table.label == [0, 1, 1]
    assert result.relabelled == 0
    assert table.stable == [1, 1, 0]


def test_a_unit_locks_on_the_second_unchanged_pass(tmp_path):
    params = ClusteringParams(**{**PARAMS.__dict__, "max_speakers": 4})
    clustering = _clustering_with(A, B, params=params)
    table, bank = _table(tmp_path, [(A, 0, False), (B, 1, False)], held=3)

    recluster(table, bank, clustering, RECLUSTER, t_now=10.0)
    assert table.locked == [False, False]
    result = recluster(table, bank, clustering, RECLUSTER, t_now=20.0)
    assert table.locked == [True, True]
    assert result.newly_locked == 2


def test_a_relabelled_unit_starts_its_count_again(tmp_path):
    params = ClusteringParams(**{**PARAMS.__dict__, "max_speakers": 4})
    clustering = _clustering_with(A, B, params=params)
    table, bank = _table(
        tmp_path,
        [(A, 0, False), (A, 0, False), (_unit(1, 0.05), 1, False)],
        held=3,
    )
    # One unchanged pass already behind the split-off unit: its relabel must
    # not count as a second one.
    table.stable[2] = 1
    recluster(table, bank, clustering, RECLUSTER, t_now=10.0)

    assert table.label == [0, 0, 0]
    assert table.stable[2] == 0
    assert table.locked[2] is False


def _aged_table(tmp_path, units: list[tuple[np.ndarray, int, bool, float]], held: int):
    """Like ``_table``, with each unit's start time."""
    table, bank = _table(
        tmp_path, [(vector, label, locked) for vector, label, locked, _ in units], held
    )
    table.t0 = [t0 for *_, t0 in units]
    return table, bank


def _pass_table(t0, locked, labels, long):
    table = UnitTable(4, DIM)
    table.t0, table.locked, table.label, table.long = t0, locked, labels, long
    return table


def test_a_pass_takes_recent_units_and_a_few_anchors_per_old_label():
    params = replace(RECLUSTER, recent_s=100.0, max_units=3, anchors=1)
    table = _pass_table(
        t0=[0.0, 10.0, 20.0, 30.0, 950.0, 960.0, 5.0],
        locked=[True, True, True, True, True, True, False],
        labels=[0, 0, 1, 1, 0, 1, 2],
        long=[1] * 7,
    )

    chosen = _units_in_pass(table, 1000.0, params)

    # Unlocked and recent units, then each old label's newest unit.
    assert chosen.tolist() == [False, True, False, True, True, True, True]
    # Within the window but over the cap: the oldest locked units give way
    # first, and their labels keep their anchors.
    wide = replace(params, recent_s=1000.0, max_units=4)
    assert _units_in_pass(table, 1000.0, wide).tolist() == [
        False,
        True,
        True,
        True,
        True,
        True,
        True,
    ]


def test_a_labels_anchors_are_its_newest_long_units():
    # Only long units found clusters, so a label whose newest old units were
    # all short must still bring long ones.
    params = replace(RECLUSTER, recent_s=100.0, max_units=10, anchors=2)
    table = _pass_table(
        t0=[0.0, 10.0, 20.0, 30.0, 40.0],
        locked=[True] * 5,
        labels=[0] * 5,
        long=[1, 1, 1, 0, 0],
    )

    chosen = _units_in_pass(table, 1000.0, params)

    assert chosen.tolist() == [False, True, True, False, False]


def test_a_label_heard_only_long_ago_still_claims_its_speaker(tmp_path):
    # Speaker A spoke long ago (locked, label 0) and B since. A returns, and
    # the online step split A's return onto label 2. The pass must give it
    # back label 0, from the anchors of A's past alone.
    params = ClusteringParams(**{**PARAMS.__dict__, "max_speakers": 5})
    clustering = _clustering_with(A, B, _unit(1, 0.05), params=params)
    old_a = [(_unit(1, 0.01 * k), 0, True, float(k)) for k in range(6)]
    recent_b = [(B, 1, True, 1900.0), (B, 1, True, 1910.0)]
    back = [(_unit(1, 0.05), 2, False, 1990.0), (_unit(1, 0.04), 2, False, 1995.0)]
    bounded = replace(RECLUSTER, recent_s=100.0, max_units=10, anchors=2)
    table, bank = _aged_table(tmp_path, old_a + recent_b + back, held=4)
    linkage_rows = []
    real_linkage = recluster_module.linkage

    def counting_linkage(points, **kwargs):
        linkage_rows.append(len(points))
        return real_linkage(points, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(recluster_module, "linkage", counting_linkage)
        recluster(table, bank, clustering, bounded, t_now=2000.0)

    assert table.label[-2:] == [0, 0]
    # The four recent units and A's two newest old ones.
    assert linkage_rows == [6]
    # The centroids still sum every unit, in the pass or not.
    vectors = bank.read().astype(np.float64)
    assert np.allclose(clustering.centers[0], vectors[[0, 1, 2, 3, 4, 5, 8, 9]].sum(0))
