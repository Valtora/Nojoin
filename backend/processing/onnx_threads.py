# Sizing ONNX Runtime's intra-op thread pool to the CPUs this process may use.
#
# Left at its default, ONNX Runtime gives every session one thread per core of the
# host and pins each thread to its core. It cannot see a container's CPU limit: in a
# 6-CPU container on a 16-core host each session starts 16 spinning threads on 6
# CPUs, the pins that fall outside the cpuset fail with "pthread_setaffinity_np
# failed", and CPU transcription takes two to three times as long (#429). An explicit
# pool size turns the pinning off, so the pool runs on whichever CPUs it is allowed.
#
# The OMP_NUM_THREADS that compose sets does not reach ONNX Runtime, which is built
# without OpenMP.
#
# The text embedding model takes the same count through fastembed's threads
# argument (#446). fastembed sets the inter-op pool to the same value, which has no
# effect: ONNX Runtime creates that pool only for parallel execution, and fastembed
# leaves its sessions sequential.

import logging
import math
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import onnxruntime as rt

logger = logging.getLogger(__name__)

# ONNX Runtime 1.29 and later read this for a session whose pool size was left at
# the default. It stays the operator's override: when it is set, Nojoin sets nothing.
INTRA_OP_THREADS_ENV = "ORT_INTRA_OP_NUM_THREADS"

_PROC_SELF_CGROUP = "/proc/self/cgroup"
_CGROUP_ROOT = "/sys/fs/cgroup"


def _read_cpu_max(directory: str) -> int | None:
    """The whole CPUs one cgroup's ``cpu.max`` allows, rounded up; None if unlimited."""
    try:
        with open(os.path.join(directory, "cpu.max")) as f:
            quota, period = f.read().split()
        if quota == "max":
            return None
        return max(1, math.ceil(int(quota) / int(period)))
    except (OSError, ValueError, ZeroDivisionError):
        return None


def cgroup_cpu_limit() -> int | None:
    """The tightest cgroup v2 CPU quota over this process, in whole CPUs.

    Walks from the process's own cgroup up to the root, because a quota on a
    parent (a Kubernetes pod, a systemd slice) binds as hard as one on the
    process's own group. Returns None on cgroup v1, where only the affinity mask
    is read, and when no level sets a quota.
    """
    try:
        with open(_PROC_SELF_CGROUP) as f:
            entries = f.read().splitlines()
    except OSError:
        return None
    relative = next((e[3:] for e in entries if e.startswith("0::")), None)
    if relative is None:
        return None

    root = os.path.normpath(_CGROUP_ROOT)
    directory = os.path.normpath(os.path.join(root, relative.lstrip("/")))
    limits: list[int] = []
    while True:
        limit = _read_cpu_max(directory)
        if limit is not None:
            limits.append(limit)
        if directory == root or not directory.startswith(root + os.sep):
            break
        directory = os.path.dirname(directory)
    return min(limits, default=None)


def usable_cpu_count() -> int:
    """CPUs this process can run on: its affinity mask, capped by any cgroup quota."""
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):  # sched_getaffinity is Linux-only
        cpus = os.cpu_count() or 1
    limit = cgroup_cpu_limit()
    return cpus if limit is None else min(cpus, limit)


def onnx_thread_count() -> int | None:
    """The intra-op pool size that fits this process's CPU limit.

    Returns None, which keeps ONNX Runtime's default, in two cases. When
    ORT_INTRA_OP_NUM_THREADS is set, since an explicit pool size would override
    it. And when nothing holds the process below the host's CPU count, so an
    unlimited worker keeps the pinned per-core pool it has always had.
    """
    if os.environ.get(INTRA_OP_THREADS_ENV, "").strip():
        return None
    threads = usable_cpu_count()
    if threads >= (os.cpu_count() or threads):
        return None
    logger.info(
        "Sizing ONNX Runtime thread pools to %d CPU(s), the limit on this worker.",
        threads,
    )
    return threads


def onnx_session_options() -> "rt.SessionOptions | None":
    """SessionOptions carrying onnx_thread_count(), or None where that is None."""
    threads = onnx_thread_count()
    if threads is None:
        return None

    import onnxruntime as rt

    options = rt.SessionOptions()
    options.intra_op_num_threads = threads
    return options
