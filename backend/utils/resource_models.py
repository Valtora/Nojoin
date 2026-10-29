"""Read resident caches only; monitoring must never load a model or CUDA."""

import itertools
import sys
from typing import Any

from backend.models.system_public import ResidentModel


def _cache(module: str, attribute: str) -> dict:
    return getattr(sys.modules.get(module), attribute, {})


def _torch_models() -> list[tuple[str, Any]]:
    models = [
        (f"Whisper {name}", model)
        for name, model in list(
            _cache("backend.processing.engines.whisper_engine", "_model_cache").items()
        )
    ]
    for module, attribute in (
        ("nemotron_model", "_RUNTIMES"),
        ("segmentation_model", "_model_cache"),
        ("embedding_core", "_embedding_model_cache"),
    ):
        for name, wrapper in list(
            _cache(f"backend.processing.{module}", attribute).items()
        ):
            models.append((f"{module}: {name}", getattr(wrapper, "model", wrapper)))
    return models


def _tensor_estimate(name: str, model: Any, seen: set) -> ResidentModel:
    tensors = itertools.chain(model.parameters(), model.buffers())
    size = 0
    device = "cpu"
    for tensor in tensors:
        device = str(tensor.device)
        if tensor.device.type != "cuda":
            continue
        storage = tensor.untyped_storage()
        key = (device, storage.data_ptr())
        if key not in seen:
            seen.add(key)
            size += storage.nbytes()
    return ResidentModel(
        name=name, device=device, estimated_bytes=size, method="CUDA tensor storage"
    )


def resident_models() -> tuple[list[ResidentModel], dict[str, int]]:
    """CUDA storage excludes activations/workspaces; reserved is process-wide."""
    models = []
    seen: set = set()
    for name, model in _torch_models():
        models.append(_tensor_estimate(name, model, seen))
    engines = _cache("backend.processing.transcribe", "_ENGINE_REGISTRY")
    for name, engine in list(engines.items()):
        for model_id in list(getattr(engine, "_model_cache", {})):
            device = "cuda" if engine._model_on_gpu.get(model_id) else "cpu"
            models.append(
                ResidentModel(
                    name=f"{name}: {model_id}",
                    device=device,
                    estimated_bytes=None,
                    method="estimate unavailable",
                )
            )
    torch = sys.modules.get("torch")
    reserved = {}
    # is_initialized does not initialise CUDA or probe for a driver.
    if torch is not None and torch.cuda.is_initialized():
        reserved = {
            f"cuda:{index}": torch.cuda.memory_reserved(index)
            for index in range(torch.cuda.device_count())
        }
    return models, reserved
