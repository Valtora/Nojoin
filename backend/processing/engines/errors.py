"""The failure half of the transcription engine contract.

An engine's ``transcribe()`` either returns the canonical result dict, which may
legitimately carry no text when the audio holds no speech, or raises
``TranscriptionError``. Callers can therefore tell "ASR ran and heard nothing"
apart from "ASR did not run". The previous contract returned ``None`` for both a
crash and a missing file, and the finalize pipeline saved that as an empty,
completed transcript.

Kept free of heavy imports: the worker and API both import it.
"""

from __future__ import annotations

from collections.abc import Iterator

# Allocator-failure wording across torch (``torch.cuda.OutOfMemoryError``),
# onnxruntime's CUDA provider ("CUDA failure 2: out of memory") and its BFC arena
# ("Failed to allocate memory for requested buffer"), and cuBLAS. Matched
# case-insensitively against every exception in the chain.
_OUT_OF_MEMORY_MARKERS = (
    "out of memory",
    "failed to allocate memory",
    "cublas_status_alloc_failed",
)

# Longest slice of the underlying error carried into the user-facing message.
_MAX_DETAIL_CHARS = 300


class TranscriptionError(RuntimeError):
    """Raised when an engine could not produce a transcription result.

    ``str(error)`` is a human-readable message fit for the UI. ``engine`` names
    the backend that failed, and ``gpu_out_of_memory`` is True when the cause was
    the GPU running out of memory, which is the one failure worth retrying once
    after freeing the card.
    """

    def __init__(
        self, message: str, *, engine: str, gpu_out_of_memory: bool = False
    ) -> None:
        super().__init__(message)
        self.engine = engine
        self.gpu_out_of_memory = gpu_out_of_memory


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_out_of_memory(exc: BaseException) -> bool:
    """Whether ``exc`` (or anything it was raised from) is an allocation failure."""
    for error in _exception_chain(exc):
        if isinstance(error, MemoryError):
            return True
        if type(error).__name__ == "OutOfMemoryError":
            return True
        text = str(error).lower()
        if any(marker in text for marker in _OUT_OF_MEMORY_MARKERS):
            return True
    return False


def _first_line(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    detail = f"{type(exc).__name__}: {lines[0]}" if lines else type(exc).__name__
    if len(detail) > _MAX_DETAIL_CHARS:
        detail = f"{detail[: _MAX_DETAIL_CHARS - 3]}..."
    return detail


def transcription_error_from(
    exc: BaseException, *, engine: str, on_gpu: bool
) -> TranscriptionError:
    """Wrap an engine exception in a ``TranscriptionError`` with a readable message.

    Args:
        exc: The exception the engine caught.
        engine: Name of the engine that raised it.
        on_gpu: Whether the engine was running on a GPU. An allocator failure on
            a GPU run is reported as CUDA out of memory; onnxruntime's arena
            error reads the same on CPU and GPU, so the wording cannot decide it.
    """
    if on_gpu and not isinstance(exc, MemoryError) and is_out_of_memory(exc):
        return TranscriptionError(
            f"Transcription failed: the GPU ran out of memory (CUDA out of memory) "
            f"while running {engine}. Free GPU memory or choose a smaller "
            f"transcription model, then retry processing.",
            engine=engine,
            gpu_out_of_memory=True,
        )
    if is_out_of_memory(exc):
        return TranscriptionError(
            f"Transcription failed: the server ran out of memory while running "
            f"{engine}. Free memory or choose a smaller transcription model, then "
            f"retry processing.",
            engine=engine,
        )
    return TranscriptionError(
        f"Transcription failed ({engine}): {_first_line(exc)}", engine=engine
    )


def is_task_interruption(exc: BaseException) -> bool:
    """Whether ``exc`` is Celery stopping the task: a time limit or termination.

    Those are not transcription failures and must reach Celery unchanged, so
    engines and callers re-raise them instead of wrapping them.
    """
    from billiard.exceptions import Terminated
    from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded

    return isinstance(exc, (SoftTimeLimitExceeded, TimeLimitExceeded, Terminated))
