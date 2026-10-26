"""Turn units and the periodic re-cluster that corrects and locks live labels.

A unit is a run of consecutive steps in which one slot was assigned, capped at
``max_steps``. Its vector is the sum of the unit-norm embeddings of its long
members (all members when none is long). Closed units go to an append-only
bank on disk, one float32 row each.

Every ``recluster_every_s``, every unit closes and the bank is clustered again
(average-linkage AHC on cosine distance). The clusters are matched one-to-one
to the labels on display, weighted by unit length, so a cluster keeps the label
most of its speech already shows. A unit's label then follows its cluster. Two
passes in a row that leave a unit's label unchanged lock it, as does age
(``lock_age_s``). A locked unit never changes again. The online centroids are
then reset to the per-label sums, so the next steps match against the corrected
speakers.

The pass is bounded, since AHC costs time and memory quadratic in what it
clusters. It takes every unlocked unit and every locked unit younger than
``recent_s``, up to ``max_units`` (the oldest locked units give way first).
Older locked units can no longer change; of those, each label keeps
``anchors`` in the pass, its most recent long (reliable) units first, so a
speaker heard only long ago still holds their label when they speak again. The centroid reset still sums every
unit.

This is Nojoin's own code, not diart's.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.optimize import linear_sum_assignment

from .clustering import OnlineSpeakerClustering


@dataclass(frozen=True)
class ReclusterParams:
    """When and how the bank is re-clustered and units lock."""

    every_s: float
    threshold: float
    min_units: int
    max_steps: int
    lock_passes: int
    lock_age_s: float
    recent_s: float
    max_units: int
    anchors: int


class UnitBank:
    """Append-only float32 rows on disk, one per closed unit.

    ``rows`` is how many rows the saved state accounts for. A row written after
    the last state save (a task that died in between) is cut off on load.
    """

    def __init__(self, path: Path, dim: int, rows: int = 0) -> None:
        self.path = path
        self.dim = dim
        self.rows = rows
        self._pending: list[np.ndarray] | None = None
        row_bytes = dim * 4
        if path.exists() and path.stat().st_size > rows * row_bytes:
            os.truncate(path, rows * row_bytes)

    def append(self, vector: np.ndarray) -> int:
        value = vector.astype(np.float32)
        if self._pending is None:
            with open(self.path, "ab") as handle:
                handle.write(value.tobytes())
        else:
            self._pending.append(value)
        self.rows += 1
        return self.rows - 1

    def read(self) -> np.ndarray:
        if self.rows == 0:
            return np.zeros((0, self.dim), np.float32)
        pending = self._pending or []
        stored_rows = self.rows - len(pending)
        stored = (
            np.fromfile(
                self.path, dtype=np.float32, count=stored_rows * self.dim
            ).reshape(stored_rows, self.dim)
            if stored_rows
            else np.zeros((0, self.dim), np.float32)
        )
        return np.concatenate((stored, np.asarray(pending))) if pending else stored

    def stage_appends(self) -> None:
        """Defer only this run's new vectors until its epoch is accepted."""
        self._pending = []

    def publish_appends(self) -> None:
        """Append accepted vectors without copying the existing bank."""
        if self._pending:
            with open(self.path, "ab") as handle:
                handle.write(np.asarray(self._pending, dtype=np.float32).tobytes())
            self._pending.clear()

    def discard_appends(self) -> None:
        """Drop rejected vectors while leaving the accepted file untouched."""
        self.rows -= len(self._pending or [])
        self._pending = None


@dataclass
class UnitTable:
    """Open units (one per slot) and every unit's label, age and lock.

    The open-unit arrays are indexed by slot. ``label`` through ``row`` are
    indexed by unit id. ``row`` is the unit's bank row, -1 while it is open.
    """

    slots: int
    dim: int
    open_uid: np.ndarray = field(init=False)
    open_first: np.ndarray = field(init=False)
    open_last: np.ndarray = field(init=False)
    open_steps: np.ndarray = field(init=False)
    open_long: np.ndarray = field(init=False)
    open_sum_long: np.ndarray = field(init=False)
    open_sum_all: np.ndarray = field(init=False)
    label: list[int] = field(default_factory=list)
    t0: list[float] = field(default_factory=list)
    stable: list[int] = field(default_factory=list)
    locked: list[bool] = field(default_factory=list)
    steps: list[int] = field(default_factory=list)
    long: list[int] = field(default_factory=list)
    row: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.open_uid = np.full(self.slots, -1, np.int64)
        self.open_first = np.zeros(self.slots, np.int64)
        self.open_last = np.full(self.slots, -10, np.int64)
        self.open_steps = np.zeros(self.slots, np.int32)
        self.open_long = np.zeros(self.slots, np.int8)
        self.open_sum_long = np.zeros((self.slots, self.dim), np.float32)
        self.open_sum_all = np.zeros((self.slots, self.dim), np.float32)

    @property
    def count(self) -> int:
        return len(self.label)

    def close(self, slot: int, bank: UnitBank) -> None:
        uid = int(self.open_uid[slot])
        if uid < 0:
            return
        has_long = bool(self.open_long[slot])
        vector = self.open_sum_long[slot] if has_long else self.open_sum_all[slot]
        self.row[uid] = bank.append(vector)
        self.steps[uid] = int(self.open_steps[slot])
        self.long[uid] = int(has_long)
        self.open_uid[slot] = -1
        self.open_steps[slot] = 0
        self.open_long[slot] = 0
        self.open_sum_long[slot] = 0
        self.open_sum_all[slot] = 0

    def close_all(self, bank: UnitBank) -> None:
        for slot in np.where(self.open_uid >= 0)[0]:
            self.close(int(slot), bank)

    def _open(self, slot: int, step: int, t_now: float) -> None:
        self.open_uid[slot] = self.count
        self.label.append(slot)
        self.t0.append(t_now)
        self.stable.append(0)
        self.locked.append(False)
        self.steps.append(0)
        self.long.append(0)
        self.row.append(-1)
        self.open_first[slot] = step

    def _add(self, slot: int, embedding: np.ndarray, is_long: bool) -> None:
        self.open_sum_all[slot] += embedding
        if is_long:
            self.open_sum_long[slot] += embedding
            self.open_long[slot] = 1

    def observe(self, observation: StepObservation, bank: UnitBank) -> dict[int, int]:
        """Extend or open a unit for each slot assigned this step; close the rest.

        Returns the unit id of every slot seen this step.
        """
        step = observation.step
        long_local = observation.segmentation.mean(axis=0) >= observation.rho_update
        seen: set[int] = set()
        for local, slot in observation.pairs:
            embedding = observation.embeddings[local]
            if slot in seen:  # a split-guard share: the same unit
                self._add(slot, embedding, bool(long_local[local]))
                continue
            seen.add(slot)
            uid = int(self.open_uid[slot])
            if uid >= 0 and (
                self.open_last[slot] != step - 1
                or self.open_steps[slot] >= observation.max_steps
            ):
                self.close(slot, bank)
            if self.open_uid[slot] < 0:
                self._open(slot, step, observation.t_now)
            self.open_last[slot] = step
            self.open_steps[slot] += 1
            self._add(slot, embedding, bool(long_local[local]))
        for slot in np.where(self.open_uid >= 0)[0]:
            if int(slot) not in seen and self.open_last[slot] < step:
                self.close(int(slot), bank)
        return {slot: int(self.open_uid[slot]) for slot in seen}


@dataclass
class StepObservation:
    """One step's assignments, as the unit table needs them."""

    pairs: list[tuple[int, int]]
    embeddings: np.ndarray
    segmentation: np.ndarray
    step: int
    t_now: float
    rho_update: float
    max_steps: int


@dataclass
class ReclusterResult:
    """What one pass did, for the step's metric."""

    units: int = 0
    clusters: int = 0
    relabelled: int = 0
    newly_locked: int = 0


def _cluster_units(
    vectors: np.ndarray, reliable: np.ndarray, params: ReclusterParams
) -> np.ndarray:
    """Cluster ids per unit: AHC over reliable units, the rest to the nearest cluster.

    Clusters with fewer than ``min_units`` reliable units are dropped (unless
    every cluster is that small), and their units go to the nearest kept one.
    """
    normed = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    tree = linkage(normed[reliable], method="average", metric="cosine")
    reliable_ids = fcluster(tree, t=params.threshold, criterion="distance") - 1
    ids, counts = np.unique(reliable_ids, return_counts=True)
    big = ids[counts >= params.min_units] if (counts >= params.min_units).any() else ids
    centroids = np.stack([normed[reliable][reliable_ids == k].mean(0) for k in big])
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)
    labels = big[np.argmax(normed @ centroids.T, axis=1)]
    keep_own = np.zeros(len(vectors), bool)
    keep_own[np.where(reliable)[0]] = np.isin(reliable_ids, big)
    labels[keep_own] = reliable_ids[np.isin(reliable_ids, big)]
    return labels


def _name_clusters(
    clusters: np.ndarray,
    current: np.ndarray,
    weights: np.ndarray,
    reserved: set[int],
    held_slot: int,
) -> dict[int, int]:
    """Map each cluster id to a display label.

    Clusters are matched one-to-one to the labels on display by the speech
    they share. An unmatched cluster takes the lowest label in use nowhere. A
    cluster that finds no label below the held slot maps to -1 and its units
    keep the labels they have.
    """
    cluster_ids = np.unique(clusters)
    shown = current != held_slot
    display_ids = np.unique(current[shown])
    names: dict[int, int] = {}
    if len(display_ids):
        overlap = np.zeros((len(cluster_ids), len(display_ids)))
        cluster_index = {c: i for i, c in enumerate(cluster_ids)}
        display_index = {d: i for i, d in enumerate(display_ids)}
        rows = np.array([cluster_index[c] for c in clusters[shown]])
        cols = np.array([display_index[d] for d in current[shown]])
        np.add.at(overlap, (rows, cols), weights[shown])
        for i, j in zip(*linear_sum_assignment(-overlap)):
            if overlap[i, j] > 0:
                names[int(cluster_ids[i])] = int(display_ids[j])
    taken = set(names.values()) | set(current.tolist()) | reserved
    candidate = 0
    for cluster in cluster_ids:
        if int(cluster) in names:
            continue
        while candidate in taken:
            candidate += 1
        names[int(cluster)] = candidate if candidate < held_slot else -1
        taken.add(candidate)
    return names


def _units_in_pass(
    table: UnitTable, t_now: float, params: ReclusterParams
) -> np.ndarray:
    """The units one pass clusters: unlocked or recent, then each label's anchors."""
    t0 = np.array(table.t0)
    locked = np.array(table.locked, bool)
    labels = np.array(table.label)
    reliable = np.array(table.long) > 0
    chosen = ~locked | (t0 >= t_now - params.recent_s)
    excess = int(chosen.sum()) - params.max_units
    if excess > 0:
        oldest_locked = np.where(chosen & locked)[0]
        oldest_locked = oldest_locked[np.argsort(t0[oldest_locked], kind="stable")]
        chosen[oldest_locked[:excess]] = False
    older = ~chosen
    for label in np.unique(labels[older]):
        units = np.where(older & (labels == label))[0]
        # Long units first (only they found clusters), newest first within each.
        best = units[np.lexsort((t0[units], reliable[units]))[-params.anchors :]]
        chosen[best] = True
    return chosen


def recluster(
    table: UnitTable,
    bank: UnitBank,
    clustering: OnlineSpeakerClustering,
    params: ReclusterParams,
    t_now: float,
) -> ReclusterResult:
    """One bounded pass over the bank: relabel, lock, then reset the centroids."""
    table.close_all(bank)
    result = ReclusterResult(units=table.count)
    if table.count < 2:
        return result
    vectors = bank.read()[np.array(table.row)].astype(np.float64)
    t0 = np.array(table.t0)
    current = np.array(table.label)
    units = np.where(_units_in_pass(table, t_now, params))[0]
    reliable = np.array(table.long)[units] > 0
    if reliable.sum() < 2:
        return result

    clusters = _cluster_units(vectors[units], reliable, params)
    held_slot = clustering.held_slot
    names = _name_clusters(
        clusters,
        current[units],
        np.array(table.steps, np.float64)[units],
        reserved=set(np.where(clustering.active)[0].tolist()),
        held_slot=held_slot,
    )
    result.clusters = len(names)
    for unit, cluster in zip(units.tolist(), clusters.tolist()):
        if table.locked[unit]:
            continue
        new_label = names[int(cluster)]
        if new_label < 0:
            new_label = int(current[unit])
        if current[unit] == held_slot:  # a held unit's first label
            table.stable[unit] = 0
        elif new_label != current[unit]:
            table.stable[unit] = 0
            result.relabelled += 1
        else:
            table.stable[unit] += 1
        table.label[unit] = new_label
        if (
            table.stable[unit] >= params.lock_passes
            or t_now - t0[unit] >= params.lock_age_s
        ):
            table.locked[unit] = True
            result.newly_locked += 1

    labels = np.array(table.label)
    clustering.reset_centers(
        {
            int(label): vectors[labels == label].sum(axis=0)
            for label in np.unique(labels)
            if label != held_slot
        }
    )
    return result
