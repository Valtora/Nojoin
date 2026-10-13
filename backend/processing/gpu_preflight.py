"""Hide GPUs that this image's CUDA build cannot drive, before the worker starts.

The worker image is built on CUDA 13.0. That needs NVIDIA driver 580 or newer, and
its kernels cover compute capability 7.5 (Turing) upwards. A host that falls short
still passes the checks the processing code relies on: the device nodes exist, so
ONNX Runtime is asked for its CUDA provider, and on a Pascal card
``torch.cuda.is_available()`` is True although no kernel can launch. Each recording
would then fail on its first GPU operation.

backend/worker_entrypoint.sh runs this module before Celery starts. When a GPU is
unusable it prints the CUDA_VISIBLE_DEVICES value that keeps only the usable ones
(empty when none are) and exits with HIDE_EXIT_CODE. The entrypoint exports that
value, so torch and ONNX Runtime both see only usable GPUs and anything else runs on
the CPU. The reason goes to stderr, which is the container log.

    python -m backend.processing.gpu_preflight
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from backend.processing.onnx_providers import gpu_is_present

MIN_COMPUTE_CAPABILITY = (7, 5)
MIN_DRIVER_MAJOR = 580
HIDE_EXIT_CODE = 3

_CPU_NOTE = "Processing on this worker runs on the CPU."

_DRIVER_VERSION_FILE = Path("/proc/driver/nvidia/version")
_DRIVER_VERSION_RE = re.compile(r"Kernel Module.*?\s(\d+(?:\.\d+)+)\s")


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    capability: tuple[int, int]
    # CUDA_VISIBLE_DEVICES form, "GPU-<uuid>": unlike an index, it names the same
    # card whatever the operator has already set CUDA_VISIBLE_DEVICES to.
    uuid: str


@dataclass(frozen=True)
class Decision:
    # None leaves CUDA_VISIBLE_DEVICES alone; a string, even empty, replaces it.
    visible: str | None
    messages: tuple[str, ...]


def driver_major(version: str | None) -> int | None:
    if not version:
        return None
    head = version.split(".", 1)[0]
    return int(head) if head.isdigit() else None


def decide(
    cuda_available: bool,
    devices: Sequence[Device],
    driver: str | None,
    torch_cuda: str | None,
) -> Decision:
    """Choose which GPUs the worker may use, given what torch reported."""
    if not cuda_available:
        major = driver_major(driver)
        if major is not None and major < MIN_DRIVER_MAJOR:
            cause = (
                f"NVIDIA driver {driver} is older than {MIN_DRIVER_MAJOR}, which "
                f"this image's CUDA {torch_cuda} build needs. Update the host driver."
            )
        else:
            cause = (
                f"CUDA {torch_cuda} cannot use it (driver {driver or 'unknown'}). "
                "Check the driver and the NVIDIA Container Toolkit setup."
            )
        return Decision(
            "", (f"An NVIDIA GPU is attached but unusable: {cause}", _CPU_NOTE)
        )

    unusable = [d for d in devices if d.capability < MIN_COMPUTE_CAPABILITY]
    if not unusable:
        return Decision(None, ())

    usable = [d for d in devices if d.capability >= MIN_COMPUTE_CAPABILITY]
    floor = ".".join(str(part) for part in MIN_COMPUTE_CAPABILITY)
    messages = [
        f"GPU {d.index} ({d.name}) has compute capability "
        f"{d.capability[0]}.{d.capability[1]}, below the {floor} (Turing) that this "
        f"image's CUDA {torch_cuda} build supports. It is hidden from the worker."
        for d in unusable
    ]
    if not usable:
        messages.append(_CPU_NOTE)
    return Decision(",".join(d.uuid for d in usable), tuple(messages))


def read_driver_version() -> str | None:
    try:
        match = _DRIVER_VERSION_RE.search(_DRIVER_VERSION_FILE.read_text())
    except OSError:
        match = None
    if match:
        return match.group(1)
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = result.stdout.strip().splitlines()
    return first[0].strip() if first else None


def main() -> int:
    # The CPU lanes have no GPU attached, so they leave here without importing torch.
    if not gpu_is_present():
        return 0

    import torch

    available = torch.cuda.is_available()
    devices = []
    if available:
        for index in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(index)
            devices.append(
                Device(
                    index, props.name, (props.major, props.minor), f"GPU-{props.uuid}"
                )
            )

    decision = decide(available, devices, read_driver_version(), torch.version.cuda)
    for message in decision.messages:
        print(f"[gpu-preflight] {message}", file=sys.stderr)
    if decision.visible is None:
        return 0
    print(decision.visible)
    return HIDE_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
