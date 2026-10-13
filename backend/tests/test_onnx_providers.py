from __future__ import annotations

import logging

import onnxruntime as rt

from backend.processing import onnx_providers
from backend.processing.onnx_providers import (
    CUDA_PROVIDER,
    iter_inference_sessions,
    verify_gpu_providers,
)

CPU_PROVIDER = "CPUExecutionProvider"
REQUESTED = [CUDA_PROVIDER, CPU_PROVIDER]


class StubSession(rt.InferenceSession):
    """A real InferenceSession subclass that skips loading an actual model.

    The helper matches on isinstance(obj, rt.InferenceSession), so the stub has to
    share that type; bypassing __init__ avoids needing an .onnx file on disk.
    """

    def __init__(self, providers: list[str]) -> None:  # noqa: D107
        self._providers = providers

    def get_providers(self) -> list[str]:  # type: ignore[override]
        return self._providers


def cuda_session() -> StubSession:
    return StubSession([CUDA_PROVIDER, CPU_PROVIDER])


def cpu_session() -> StubSession:
    return StubSession([CPU_PROVIDER])


class ModelStub:
    """Stands in for a loaded onnx-asr / fastembed model wrapper."""

    def __init__(self, **sessions: object) -> None:
        self.__dict__.update(sessions)


def test_finds_sessions_across_attributes_containers_and_nesting():
    inner = ModelStub(_decoder=cuda_session())
    model = ModelStub(
        _encoder=cuda_session(),
        _parts=[cuda_session()],
        _by_name={"joiner": cuda_session()},
        _nested=inner,
        _unrelated="not a session",
    )

    # One plain attribute, one inside a list, one inside a dict, one a level deeper.
    assert len(list(iter_inference_sessions(model))) == 4


def test_tolerates_reference_cycles():
    model = ModelStub(_model=cuda_session())
    model.__dict__["_self"] = model

    assert len(list(iter_inference_sessions(model))) == 1


def test_returns_true_when_every_session_is_on_cuda(caplog):
    model = ModelStub(_encoder=cuda_session(), _decoder=cuda_session())

    with caplog.at_level(logging.INFO):
        assert verify_gpu_providers(model, component="Canary", requested=REQUESTED)

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_warns_when_a_session_fell_back_to_cpu_on_a_gpu_host(caplog, monkeypatch):
    # The exact production failure: a GPU is attached, but the CUDA provider could
    # not be loaded, so onnxruntime built on CPU and reported success anyway.
    monkeypatch.setattr(onnx_providers, "gpu_is_present", lambda: True)
    model = ModelStub(_encoder=cpu_session(), _decoder=cpu_session())

    with caplog.at_level(logging.INFO):
        assert not verify_gpu_providers(model, component="Canary", requested=REQUESTED)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "despite a GPU being present" in warnings[0].message
    assert "libonnxruntime_providers_cuda.so" in warnings[0].message


def test_warns_when_only_some_sessions_fell_back(caplog, monkeypatch):
    monkeypatch.setattr(onnx_providers, "gpu_is_present", lambda: True)
    model = ModelStub(_encoder=cuda_session(), _decoder=cpu_session())

    with caplog.at_level(logging.WARNING):
        assert not verify_gpu_providers(model, component="Canary", requested=REQUESTED)

    assert "1 of 2 ONNX Runtime session(s) are on CPU" in caplog.records[0].message


def test_cpu_only_host_is_reported_as_expected_not_as_a_fault(caplog, monkeypatch):
    # The CPU-only worker lanes and CPU-only deployments both land here. Running on
    # CPU is the intended outcome, so it must not raise a warning on every load.
    monkeypatch.setattr(onnx_providers, "gpu_is_present", lambda: False)
    model = ModelStub(_encoder=cpu_session(), _decoder=cpu_session())

    with caplog.at_level(logging.INFO):
        assert not verify_gpu_providers(model, component="Canary", requested=REQUESTED)

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "as expected with no GPU attached" in caplog.records[0].message


def test_gpu_presence_is_detected_from_device_nodes(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(onnx_providers.glob, "glob", lambda pattern: [])
    assert not onnx_providers.gpu_is_present()

    monkeypatch.setattr(
        onnx_providers.glob, "glob", lambda pattern: ["/dev/nvidia0", "/dev/nvidiactl"]
    )
    assert onnx_providers.gpu_is_present()


def test_gpus_hidden_by_cuda_visible_devices_count_as_absent(monkeypatch):
    # The worker entrypoint hides a GPU this image cannot drive this way, and the
    # device nodes are still there, so the environment has to win.
    monkeypatch.setattr(onnx_providers.glob, "glob", lambda pattern: ["/dev/nvidia0"])
    for hidden in ("", "-1"):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", hidden)
        assert not onnx_providers.gpu_is_present()

    monkeypatch.setenv(
        "CUDA_VISIBLE_DEVICES", "GPU-552ae5c9-a6aa-9229-3016-d1b8617e1a65"
    )
    assert onnx_providers.gpu_is_present()


def test_stays_quiet_when_cuda_was_never_requested(caplog):
    model = ModelStub(_encoder=cpu_session())

    with caplog.at_level(logging.WARNING):
        assert not verify_gpu_providers(
            model, component="Canary", requested=[CPU_PROVIDER]
        )

    assert not caplog.records


def test_no_sessions_found_is_not_reported_as_success(caplog):
    with caplog.at_level(logging.WARNING):
        assert not verify_gpu_providers(
            ModelStub(), component="Canary", requested=REQUESTED
        )

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_get_available_providers_is_not_a_substitute_for_this_check():
    """Documents why this module exists.

    get_available_providers() reports what the onnxruntime build was compiled with,
    not what can actually be instantiated, so it lists CUDAExecutionProvider even
    where no CUDA session can be created. Any health check built on it would have
    reported the GPU as healthy throughout the CPU-fallback regression.
    """
    if CUDA_PROVIDER not in rt.get_available_providers():
        # A CPU-only onnxruntime build; nothing to assert about the trap.
        return

    session_providers = cpu_session().get_providers()
    assert CUDA_PROVIDER not in session_providers


def test_asr_engine_picks_fp32_on_gpu_and_int8_on_cpu(monkeypatch):
    """int8 has no CUDA kernels, so the engine must not request it on a GPU host."""
    import sys
    import types

    from backend.processing.engines import onnx_asr_engine

    captured: dict = {}
    stub = types.ModuleType("onnx_asr")
    stub.load_model = lambda onnx_id, **kw: captured.update(kw) or ModelStub()
    monkeypatch.setitem(sys.modules, "onnx_asr", stub)

    class Engine(onnx_asr_engine.OnnxAsrEngine):
        name = "canary"
        config_key = "canary_model"
        default_model_id = "nemo-canary-1b-v2"
        onnx_id_map: dict = {}

    for gpu_present, expected in ((True, None), (False, "int8")):
        monkeypatch.setattr(onnx_asr_engine, "gpu_is_present", lambda v=gpu_present: v)
        engine = Engine()
        engine._model_cache = {}
        engine._get_model({})
        assert captured["quantization"] == expected


def test_asr_engine_requests_cuda_only_when_a_gpu_is_present(monkeypatch):
    """Requesting CUDA with no device attached segfaults the process.

    onnxruntime-gpu loads its CUDA provider library, finds no device, and dies with
    SIGSEGV. A native fault cannot be caught, so this has to be prevented rather
    than handled. The CPU-only deployment in docs/DEPLOYMENT.md runs the GPU lane
    on this image with no device, which is that shape exactly.
    """
    import sys
    import types

    from backend.processing.engines import onnx_asr_engine

    captured: dict = {}
    stub = types.ModuleType("onnx_asr")
    stub.load_model = lambda onnx_id, **kw: captured.update(kw) or ModelStub()
    monkeypatch.setitem(sys.modules, "onnx_asr", stub)

    class Engine(onnx_asr_engine.OnnxAsrEngine):
        name = "canary"
        config_key = "canary_model"
        default_model_id = "nemo-canary-1b-v2"
        onnx_id_map: dict = {}

    monkeypatch.setattr(onnx_asr_engine, "gpu_is_present", lambda: False)
    engine = Engine()
    engine._model_cache = {}
    engine._get_model({})
    assert captured["providers"] == [CPU_PROVIDER]

    monkeypatch.setattr(onnx_asr_engine, "gpu_is_present", lambda: True)
    engine._model_cache = {}
    engine._get_model({})
    assert captured["providers"] == [CUDA_PROVIDER, CPU_PROVIDER]


def test_text_embedding_requests_cuda_only_when_a_gpu_is_present(monkeypatch):
    """Same native crash, on the lane that actually hit it in the field.

    Text embedding runs on the io and parse lanes, neither of which compose grants
    a GPU, so an unconditional CUDA request killed every indexing task there and
    left context_chunks empty while the recording still read as processed.
    """
    import sys
    import types

    from backend.processing import text_embedding

    captured: dict = {}
    stub = types.ModuleType("fastembed")
    stub.TextEmbedding = lambda **kw: captured.update(kw) or ModelStub()
    monkeypatch.setitem(sys.modules, "fastembed", stub)

    monkeypatch.setattr(text_embedding, "gpu_is_present", lambda: False)
    text_embedding.TextEmbeddingService()
    assert captured["providers"] == [CPU_PROVIDER]

    monkeypatch.setattr(text_embedding, "gpu_is_present", lambda: True)
    text_embedding.TextEmbeddingService()
    assert captured["providers"] == [CUDA_PROVIDER, CPU_PROVIDER]


def test_window_cap_tightens_on_gpu_and_respects_test_overrides(monkeypatch):
    """fp32 attention activations blow an 8GB card at the CPU-sized window."""
    from backend.processing.engines import onnx_asr_engine as e

    monkeypatch.setattr(e, "gpu_is_present", lambda: False)
    assert e.max_chunk_duration_s() == e.MAX_CHUNK_DURATION_S

    monkeypatch.setattr(e, "gpu_is_present", lambda: True)
    assert e.max_chunk_duration_s() == e.GPU_MAX_CHUNK_DURATION_S

    # Tests shrink MAX_CHUNK_DURATION_S to keep fixtures tiny; that must still win.
    monkeypatch.setattr(e, "MAX_CHUNK_DURATION_S", 4.0)
    assert e.max_chunk_duration_s() == 4.0


def test_gpu_window_follows_the_install_setting(monkeypatch, caplog):
    """An administrator sizes the GPU window for the card through config.json;
    the merged settings a run is given carry that key."""
    from backend.processing.engines import onnx_asr_engine as e

    monkeypatch.setattr(e, "gpu_is_present", lambda: True)

    assert e.max_chunk_duration_s({"gpu_asr_window_s": 60}) == 60.0
    assert e.max_chunk_duration_s({"gpu_asr_window_s": 240.0}) == 240.0
    # Unset means the shipped default.
    assert (
        e.max_chunk_duration_s({"gpu_asr_window_s": None}) == e.GPU_MAX_CHUNK_DURATION_S
    )
    assert e.max_chunk_duration_s({}) == e.GPU_MAX_CHUNK_DURATION_S

    # A value the engine cannot use never fails a run: logged, default used.
    for bad in (10, 500, "90", True, float("nan")):
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert (
                e.max_chunk_duration_s({"gpu_asr_window_s": bad})
                == e.GPU_MAX_CHUNK_DURATION_S
            )
        assert "Ignoring invalid gpu_asr_window_s" in caplog.text

    # The CPU cap is a different constraint and ignores the GPU setting.
    monkeypatch.setattr(e, "gpu_is_present", lambda: False)
    assert e.max_chunk_duration_s({"gpu_asr_window_s": 60}) == e.MAX_CHUNK_DURATION_S


def test_gpu_window_reads_config_json_when_no_settings_are_passed(monkeypatch):
    from backend.processing.engines import onnx_asr_engine as e

    monkeypatch.setattr(e, "gpu_is_present", lambda: True)
    monkeypatch.setattr(
        e.config_manager,
        "get",
        lambda key, default=None: 45 if key == "gpu_asr_window_s" else default,
    )
    assert e.max_chunk_duration_s() == 45.0


def test_chunk_boundaries_use_the_window_they_are_given():
    import numpy as np

    from backend.processing.engines import onnx_asr_engine as e

    sample_rate = 100
    audio = np.zeros(sample_rate * 100, dtype="float32")

    boundaries = e._chunk_boundaries(audio, sample_rate, window_s=40.0)

    # Snapping may pull a cut earlier within its radius, never later, so no
    # window exceeds the one given, and the windows tile the whole signal.
    assert all(end - start <= 40 * sample_rate for start, end in boundaries)
    assert boundaries[0][0] == 0 and boundaries[-1][1] == len(audio)
    assert all(a[1] == b[0] for a, b in zip(boundaries, boundaries[1:]))
    assert len(boundaries) >= 3
