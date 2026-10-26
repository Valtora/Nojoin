"""Native preparation and conversion failures preserve the completed baseline."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.processing import diarization_union, nemotron_model
from backend.tests import test_final_diarization as fixtures
from backend.utils import nemotron_model_utils as assets

lane = fixtures.lane
pytestmark = pytest.mark.usefixtures("stub_celery_dispatch")


@pytest.mark.parametrize(
    ("boundary", "failure"),
    [
        ("inspect", ValueError("bad asset config")),
        ("release", RuntimeError("CUDA release failed")),
        ("read", OSError("bad PCM file")),
        ("load", ImportError("native dependency unavailable")),
        ("load", MemoryError("model allocation failed")),
        ("load", OSError("asset hash verification failed")),
        ("union", TypeError("malformed intervals")),
    ],
)
def test_native_failure_boundaries_keep_baseline_without_retry(
    lane: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    failure: Exception,
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        lane.calls.append(f"failed:{boundary}")
        raise failure

    targets = {
        "inspect": (assets, "inspect_nemotron_model"),
        "release": (fixtures.final, "_release_community"),
        "read": (fixtures.final, "_read_audio"),
        "load": (nemotron_model, "load_nemotron_model"),
        "union": (diarization_union, "union_diarization"),
    }
    module, name = targets[boundary]
    monkeypatch.setattr(module, name, fail)
    result, status = fixtures._run(lane)
    assert result is lane.community
    assert status == {"route": "failed", "reason": type(failure).__name__}
    assert lane.calls.count(f"failed:{boundary}") == 1
    assert lane.calls.count("clear") == 1
    if boundary in {"inspect", "release", "read"}:
        assert "cuda:1" not in lane.calls
