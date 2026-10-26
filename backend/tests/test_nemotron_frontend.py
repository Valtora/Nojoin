"""The equivalence guard rejects feature, mask, shape and non-finite drift."""

from __future__ import annotations

from typing import Any

import pytest

from backend.processing.nemotron_frontend import check_feature_equivalence
from backend.processing.nemotron_types import NemotronStateError


def _features() -> dict[str, Any]:
    import torch

    return {
        "input_features": torch.ones(1, 3, 128),
        "attention_mask": torch.ones(1, 3, dtype=torch.bool),
    }


@pytest.mark.parametrize("drift", ["features", "mask", "shape", "nan"])
def test_equivalence_failure_is_an_error_instead_of_ignored_failure_count(
    drift: str,
) -> None:
    reference, actual = _features(), _features()
    if drift == "features":
        actual["input_features"][0, 0, 0] += 0.001
    if drift == "mask":
        actual["attention_mask"][0, 0] = False
    if drift == "shape":
        actual["input_features"] = actual["input_features"][:, :-1]
    if drift == "nan":
        actual["input_features"][0, 0, 0] = float("nan")
    with pytest.raises(NemotronStateError, match="CPU/CUDA"):
        check_feature_equivalence(reference, actual)


def test_recorded_feature_tolerance_is_used_without_widening() -> None:
    reference, actual = _features(), _features()
    actual["input_features"][0, 0, 0] += 0.00019
    check_feature_equivalence(reference, actual)
