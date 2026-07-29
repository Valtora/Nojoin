# nojoin/processing/onnx_providers.py
# Verification that ONNX Runtime sessions got the execution providers we asked for.
#
# ONNX Runtime treats its `providers` argument as a preference, not a contract. When a
# requested provider cannot be instantiated -- a CUDA or cuDNN shared library missing
# from the loader path being the usual cause -- it is dropped and the session is built
# on the next provider in the list. A total loss of GPU acceleration therefore surfaces
# only as "still works, just slower", which is easy to miss for a long time.
#
# onnxruntime.get_available_providers() does not detect this: it reports what the build
# was compiled with, so it lists CUDAExecutionProvider even in a container with no GPU
# and no working cuDNN. The only trustworthy signal is what a constructed session
# reports, so the helpers here inspect live sessions and log a CPU fallback loudly.

import glob
import logging
from collections.abc import Iterator, Sequence
from typing import Any

logger = logging.getLogger(__name__)

CUDA_PROVIDER = "CUDAExecutionProvider"

# Character devices the NVIDIA container runtime exposes when a GPU is passed in.
_NVIDIA_DEVICE_GLOB = "/dev/nvidia*"

# How deep to walk a loaded model's attributes looking for sessions. Model wrappers
# nest them shallowly (onnx-asr hangs _encoder/_decoder off the model object, fastembed
# keeps one a couple of levels down), and a bounded walk stays cheap.
_MAX_SEARCH_DEPTH = 4


def iter_inference_sessions(
    obj: Any, _depth: int = 0, _seen: set[int] | None = None
) -> Iterator[Any]:
    """Yield every onnxruntime InferenceSession reachable from an object.

    Model loaders keep their sessions under library-specific private attribute names
    (`_model`, `_encoder`, `_decoder_joint`, ...), so this walks the object graph
    rather than binding to any one library's internals.

    Args:
        obj: The loaded model object to search.

    Yields:
        Each distinct InferenceSession found, up to a bounded search depth.
    """
    import onnxruntime as rt

    if _seen is None:
        _seen = set()
    if obj is None or _depth > _MAX_SEARCH_DEPTH or id(obj) in _seen:
        return
    _seen.add(id(obj))

    if isinstance(obj, rt.InferenceSession):
        yield obj
        return

    if isinstance(obj, (list, tuple, set, frozenset)):
        children: list[Any] = list(obj)
    elif isinstance(obj, dict):
        children = list(obj.values())
    else:
        # Read __dict__ directly so the walk never triggers a property or descriptor.
        children = list(vars(obj).values()) if hasattr(obj, "__dict__") else []

    for child in children:
        yield from iter_inference_sessions(child, _depth + 1, _seen)


def gpu_is_present() -> bool:
    """Report whether an NVIDIA GPU is visible to this process.

    Checked by device node rather than by asking a CUDA library, so it stays cheap
    and does not itself depend on the library loading correctly. Distinguishes the
    CPU-only lanes and CPU-only deployments, where running on CPU is intended, from
    a GPU host where a CPU fallback means something is misconfigured.
    """
    return bool(glob.glob(_NVIDIA_DEVICE_GLOB))


def verify_gpu_providers(
    model: Any, *, component: str, requested: Sequence[str]
) -> bool:
    """Warn when a model asked for the CUDA provider but its sessions fell back to CPU.

    Args:
        model: A loaded model object holding one or more InferenceSessions.
        component: Human-readable name for the model, used in log messages.
        requested: The provider list that was passed to the loader.

    Returns:
        True when CUDA was requested and every session is running on it. False when
        CUDA was not requested, when a session fell back to CPU, or when no session
        could be found to inspect.
    """
    if CUDA_PROVIDER not in requested:
        return False

    try:
        sessions = list(iter_inference_sessions(model))
        # Resolve the providers here too, inside the guard, so a session that objects
        # to being queried cannot take the caller down with it.
        session_providers = [s.get_providers() for s in sessions]
    except Exception as e:  # noqa: BLE001
        # Provider verification is diagnostic only and must never break model loading.
        logger.debug(f"{component}: could not inspect ONNX Runtime sessions: {e}")
        return False

    if not sessions:
        logger.debug(f"{component}: found no ONNX Runtime session to verify.")
        return False

    degraded = [p for p in session_providers if CUDA_PROVIDER not in p]
    if degraded:
        active = sorted({name for providers in degraded for name in providers})
        summary = (
            f"{component}: {len(degraded)} of {len(sessions)} ONNX Runtime session(s) "
            f"are on CPU (active: {', '.join(active)})"
        )
        if gpu_is_present():
            # A GPU is attached but ONNX Runtime is not using it, which is the
            # misconfiguration worth shouting about.
            logger.warning(
                f"{summary}, despite a GPU being present. Inference will be far "
                "slower than expected. This usually means the CUDA/cuDNN shared "
                "libraries are not on the loader path; check this log for 'Failed to "
                "load library libonnxruntime_providers_cuda.so'."
            )
        else:
            # No GPU is attached: the CPU-only lanes and CPU-only deployments both
            # land here, where running on CPU is the intended outcome, not a fault.
            logger.info(f"{summary}, as expected with no GPU attached.")
        return False

    logger.info(
        f"{component} is using the ONNX Runtime CUDA execution provider "
        f"({len(sessions)} session(s))."
    )
    return True
