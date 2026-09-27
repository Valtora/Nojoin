import sys
import types

from backend.worker.tasks.pipeline import _release_asr_vram


def _install(monkeypatch, module_name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(module_name)
    for name, value in attrs.items():
        setattr(module, name, value)
    monkeypatch.setitem(sys.modules, module_name, module)
    return module


def test_release_asr_vram_uses_rocm_pytorch_accelerator_api(monkeypatch):
    released = []
    empty_cache_calls = []
    cuda = types.SimpleNamespace(
        is_available=lambda: True,
        empty_cache=lambda: empty_cache_calls.append(True),
    )
    _install(
        monkeypatch,
        "torch",
        cuda=cuda,
        version=types.SimpleNamespace(hip="7.14"),
    )
    _install(
        monkeypatch,
        "backend.processing.transcribe",
        release_model_cache=lambda: released.append(True),
    )

    _release_asr_vram()

    assert released == [True]
    assert empty_cache_calls == [True]


def test_release_asr_vram_skips_cache_release_without_accelerator(monkeypatch):
    released = []
    cuda = types.SimpleNamespace(is_available=lambda: False)
    _install(monkeypatch, "torch", cuda=cuda)
    _install(
        monkeypatch,
        "backend.processing.transcribe",
        release_model_cache=lambda: released.append(True),
    )

    _release_asr_vram()

    assert released == []
