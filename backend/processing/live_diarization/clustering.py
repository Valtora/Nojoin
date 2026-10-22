# Vendored from diart v0.9.2, commit b140da50488471eb2adda0123116bebf54baa530
# (https://github.com/juanmc2005/diart): OnlineSpeakerClustering from
# src/diart/blocks/clustering.py, the minimisation-objective parts of SpeakerMap
# from src/diart/mapping.py, and overlapped_speech_penalty from
# src/diart/functional.py. Nojoin's changes are listed in the module docstring.
#
# MIT License
#
# Copyright (c) 2021 Université Paris-Saclay
# Copyright (c) 2021 CNRS
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""Online speaker clustering for live labels (Coria et al., ASRU 2021).

Each step, every local speaker the segmentation model found in the window joins
the nearest meeting speaker (a centroid) or founds a new one. The mapping is a
one-to-one assignment, so two local speakers never share a centroid by
assignment alone.

Changes from diart:

- **State is plain arrays** (``centers``, ``active``) so the live task can save
  and reload it on every run. ``SpeakerMap`` objects are replaced by functions
  over the mapping matrix, and pyannote.core's ``cdist`` by SciPy's, which it
  wraps for the cosine metric.
- **Held voices.** diart forces a missed local speaker too short to found a
  centroid onto the nearest free one. Here it is held instead: it is mapped to
  the reserved last slot (``held_slot``), which is never founded, matched or
  updated, and the caller shows it unlabelled until later evidence labels it.
- **Split guard.** When the segmentation model splits one voice into two local
  speakers, the one-to-one assignment leaves the second unmapped, and diart
  would found a duplicate centroid for it. A missed local speaker whose nearest
  centroid is within ``share_delta`` and was taken this step shares that
  centroid's label instead (no update).
- **No blocked centroids** and a single prototype per speaker: diart never
  blocks one either without its external merge hooks.
- ``overlapped_speech_penalty`` uses tensor methods so this module imports
  without torch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist

# diart MinimizationObjective.invalid_value: linear_sum_assignment cannot take
# inf, so an invalid pairing costs this much instead.
INVALID = 1e10


def is_mapped(matrix: np.ndarray, row: int) -> bool:
    """diart ``SpeakerMap.is_source_speaker_mapped``: the row has a valid cost."""
    return bool(matrix[row].min() != INVALID)


def valid_assignments(matrix: np.ndarray) -> list[tuple[int, int]]:
    """diart ``SpeakerMap.valid_assignments(strict=False)``.

    The optimal one-to-one assignment, keeping each local speaker (row) that has
    at least one valid cost.
    """
    rows, cols = linear_sum_assignment(matrix, maximize=False)
    return [
        (int(row), int(col))
        for row, col in zip(rows, cols)
        if is_mapped(matrix, int(row))
    ]


def unmap_threshold(matrix: np.ndarray, threshold: float) -> np.ndarray:
    """diart ``SpeakerMap.unmap_threshold``: drop assignments at or above it."""
    result = matrix.copy()
    for row, col in valid_assignments(matrix):
        if matrix[row, col] >= threshold:
            result[row] = INVALID
    return result


def overlapped_speech_penalty(segmentation: Any, gamma: float, beta: float) -> Any:
    """diart ``functional.overlapped_speech_penalty`` on a (batch, frames, speakers) tensor.

    Weights each local speaker's frames by its activation, discounted where
    other local speakers are active too, so an overlapped frame contributes
    less to that speaker's embedding.
    """
    probs = (beta * segmentation).softmax(dim=-1)
    weights = segmentation.pow(gamma) * probs.pow(gamma)
    return weights.clamp(min=1e-8)


@dataclass(frozen=True)
class ClusteringParams:
    """Thresholds of one clustering step.

    ``tau_active`` is the peak activation for a local speaker to count as
    active, ``rho_update`` the share of the window it must speak in to update
    a centroid or found one, ``delta_new`` the cosine distance beyond which it
    does not join a centroid, ``share_delta`` the split guard's distance, and
    ``max_speakers`` the slot count, including the held slot.
    """

    tau_active: float
    rho_update: float
    delta_new: float
    share_delta: float
    max_speakers: int


@dataclass
class StepAssignment:
    """Where each active local speaker of one step went.

    ``pairs`` are the assignment's (local, slot) pairs, founded centroids
    included. ``extra`` are split-guard shares and held voices (slot
    ``held_slot``); neither updates a centroid.
    """

    pairs: list[tuple[int, int]]
    extra: list[tuple[int, int]] = field(default_factory=list)
    founded: int = 0

    @property
    def all_pairs(self) -> list[tuple[int, int]]:
        return [*self.pairs, *self.extra]


class OnlineSpeakerClustering:
    """diart's constrained incremental clustering, with held voices.

    ``centers`` holds one running sum of unit-norm embeddings per slot and
    ``active`` marks the founded slots. The last slot is reserved for held
    voices. ``initialised`` is false until the first step, which founds a
    centroid for every active local speaker, as diart does.
    """

    def __init__(
        self,
        params: ClusteringParams,
        *,
        centers: np.ndarray | None = None,
        active: np.ndarray | None = None,
    ) -> None:
        self.params = params
        self.centers = centers
        size = params.max_speakers
        self.active = (
            active.astype(bool).copy() if active is not None else np.zeros(size, bool)
        )

    @property
    def initialised(self) -> bool:
        return self.centers is not None

    @property
    def held_slot(self) -> int:
        return self.params.max_speakers - 1

    @property
    def num_free(self) -> int:
        return self.params.max_speakers - int(self.active.sum()) - 1

    def _next_slot(self) -> int:
        for slot in range(self.held_slot):
            if not self.active[slot]:
                return slot
        raise RuntimeError("No free live speaker slot")

    def add_center(self, embedding: np.ndarray) -> int:
        assert self.centers is not None
        slot = self._next_slot()
        self.centers[slot] = embedding
        self.active[slot] = True
        return slot

    def reset_centers(self, sums: dict[int, np.ndarray]) -> None:
        """Replace every centroid, keeping only the slots given."""
        assert self.centers is not None
        self.centers[:] = 0.0
        self.active[:] = False
        for slot, vector in sums.items():
            self.centers[slot] = vector
            self.active[slot] = True

    def _distances(
        self, embeddings: np.ndarray, active_local: np.ndarray
    ) -> np.ndarray:
        assert self.centers is not None
        with np.errstate(invalid="ignore", divide="ignore"):
            dist = cdist(embeddings, self.centers, metric="cosine")
        dist = np.nan_to_num(dist, nan=INVALID)
        inactive_local = [
            s for s in range(embeddings.shape[0]) if s not in active_local
        ]
        dist[inactive_local, :] = INVALID
        # The held slot is never active, so this masks it too.
        dist[:, ~self.active] = INVALID
        return dist

    def identify(
        self, segmentation: np.ndarray, embeddings: np.ndarray
    ) -> StepAssignment:
        """Assign the local speakers of one window.

        ``segmentation`` is (frames, local speakers) activations and
        ``embeddings`` (local speakers, dim) unit-norm vectors.
        """
        params = self.params
        active_local = np.where(segmentation.max(axis=0) >= params.tau_active)[0]
        long_local = set(
            np.where(segmentation.mean(axis=0) >= params.rho_update)[0].tolist()
        )
        no_nan = np.where(~np.isnan(embeddings).any(axis=1))[0]
        active_local = np.intersect1d(active_local, no_nan)

        if not self.initialised:
            self.centers = np.zeros((params.max_speakers, embeddings.shape[1]))
            self.active[:] = False
            pairs = [(int(s), self.add_center(embeddings[s])) for s in active_local]
            return StepAssignment(pairs=pairs, founded=len(pairs))

        centers = self.centers
        assert centers is not None
        dist = self._distances(embeddings, active_local)
        valid = unmap_threshold(dist, params.delta_new)
        missed = [int(s) for s in active_local if not is_mapped(valid, int(s))]
        result = StepAssignment(pairs=[])

        taken = {slot for _, slot in valid_assignments(valid)}
        shared: set[int] = set()
        for local in missed:
            nearest = int(np.argmin(dist[local]))
            if dist[local, nearest] < params.share_delta and nearest in taken:
                result.extra.append((local, nearest))
                shared.add(local)

        new_local: list[int] = []
        for local in missed:
            if local in shared:
                continue
            if len(new_local) < self.num_free and local in long_local:
                new_local.append(local)
            else:
                result.extra.append((local, self.held_slot))

        result.pairs = valid_assignments(valid)
        for local, slot in result.pairs:
            if local in long_local:
                centers[slot] += embeddings[local]

        for local in new_local:
            result.pairs.append((local, self.add_center(embeddings[local])))
            result.founded += 1
        # In local-speaker order, as diart's valid_assignments reports them.
        result.pairs.sort()
        return result
