"""One clustering step and one re-cluster pass, on hand-built inputs."""

from __future__ import annotations

import numpy as np

from backend.processing.live_diarization.clustering import (
    ClusteringParams,
    OnlineSpeakerClustering,
)
from backend.processing.live_diarization.engine import LiveLabelConfig
from backend.processing.live_diarization.recluster import (
    ReclusterParams,
    UnitBank,
    UnitTable,
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


RECLUSTER = ReclusterParams(
    every_s=10.0,
    threshold=0.45,
    min_units=1,
    max_steps=10,
    lock_passes=2,
    lock_age_s=600.0,
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
