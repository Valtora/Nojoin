"""Model preparation fetches the ONNX precision the engine will load.

Preparation runs on the transcription lane. It used to fetch int8 everywhere,
while the engine loads fp32 wherever a GPU is present, so on a GPU host the
prepared files were never used and the first transcription downloaded again.
"""

from __future__ import annotations

import onnx_asr
import pytest

from backend import preload_models
from backend.processing import onnx_providers
from backend.processing.engines import onnx_asr_engine
from backend.processing.engines.canary_engine import CanaryEngine


@pytest.mark.parametrize("on_gpu", [True, False], ids=["gpu", "cpu"])
def test_preparation_loads_the_precision_the_engine_loads(on_gpu, monkeypatch):
    loads: list[dict] = []

    def record_load(model_id, **kwargs):
        loads.append({"model_id": model_id, "quantization": kwargs["quantization"]})
        return object()

    monkeypatch.setattr(onnx_asr, "load_model", record_load)
    monkeypatch.setattr(onnx_providers, "gpu_is_present", lambda: on_gpu)
    monkeypatch.setattr(onnx_asr_engine, "gpu_is_present", lambda: on_gpu)
    monkeypatch.setattr(onnx_asr_engine, "verify_gpu_providers", lambda *a, **k: False)

    engine = CanaryEngine()
    preload_models._prepare_onnx_asr_model(
        engine._to_onnx_asr_id(engine.default_model_id)
    )
    engine._get_model({})

    prepared, loaded = loads
    assert prepared == loaded
    assert loaded["quantization"] == (None if on_gpu else "int8")
