"""ONNX Runtime's thread pool follows the CPU limit the worker runs under (#429).

Left at its default, ONNX Runtime sizes every session's pool to the host's cores
and pins each thread, so a CPU-limited container ran 16 spinning threads on 6
CPUs and transcribed two to three times slower.
"""

from __future__ import annotations

import onnx_asr
import pytest

from backend import preload_models
from backend.processing import onnx_providers, onnx_threads
from backend.processing.engines import onnx_asr_engine
from backend.processing.engines.canary_engine import CanaryEngine


@pytest.fixture
def cgroup_fs(tmp_path, monkeypatch):
    """A fake cgroup v2 tree with this process at /kubepods/pod/ctr."""
    root = tmp_path / "cgroup"
    proc = tmp_path / "proc_self_cgroup"
    proc.write_text("0::/kubepods/pod/ctr\n")
    monkeypatch.setattr(onnx_threads, "_CGROUP_ROOT", str(root))
    monkeypatch.setattr(onnx_threads, "_PROC_SELF_CGROUP", str(proc))

    def set_cpu_max(relative: str, value: str) -> None:
        directory = root / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "cpu.max").write_text(value + "\n")

    return proc, set_cpu_max


def _cpus(monkeypatch, *, affinity: int, host: int) -> None:
    monkeypatch.setattr(
        onnx_threads.os, "sched_getaffinity", lambda pid: set(range(affinity))
    )
    monkeypatch.setattr(onnx_threads.os, "cpu_count", lambda: host)


@pytest.mark.parametrize(
    ("cpu_max", "expected"),
    [
        ("max 100000", None),
        ("600000 100000", 6),
        ("150000 100000", 2),  # a fractional limit rounds up
        ("50000 100000", 1),
        ("garbage", None),
    ],
)
def test_cpu_max_is_read_in_whole_cpus(cgroup_fs, cpu_max, expected):
    _, set_cpu_max = cgroup_fs
    set_cpu_max("kubepods/pod/ctr", cpu_max)

    assert onnx_threads.cgroup_cpu_limit() == expected


def test_a_quota_on_a_parent_cgroup_binds(cgroup_fs):
    _, set_cpu_max = cgroup_fs
    set_cpu_max("", "max 100000")
    set_cpu_max("kubepods/pod", "400000 100000")
    set_cpu_max("kubepods/pod/ctr", "max 100000")

    assert onnx_threads.cgroup_cpu_limit() == 4


def test_the_tightest_quota_in_the_hierarchy_wins(cgroup_fs):
    _, set_cpu_max = cgroup_fs
    set_cpu_max("kubepods/pod", "400000 100000")
    set_cpu_max("kubepods/pod/ctr", "200000 100000")

    assert onnx_threads.cgroup_cpu_limit() == 2


def test_cgroup_v1_reports_no_quota(cgroup_fs):
    proc, set_cpu_max = cgroup_fs
    proc.write_text("12:cpu,cpuacct:/docker/abc\n")
    set_cpu_max("kubepods/pod/ctr", "200000 100000")

    assert onnx_threads.cgroup_cpu_limit() is None


def test_a_missing_proc_file_reports_no_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(onnx_threads, "_PROC_SELF_CGROUP", str(tmp_path / "absent"))

    assert onnx_threads.cgroup_cpu_limit() is None


@pytest.mark.parametrize(
    ("affinity", "cpu_max", "expected"),
    [
        (6, "max 100000", 6),  # a cpuset alone
        (24, "600000 100000", 6),  # a quota alone
        (6, "400000 100000", 4),
        (2, "800000 100000", 2),
    ],
)
def test_usable_cpus_are_the_affinity_capped_by_the_quota(
    cgroup_fs, monkeypatch, affinity, cpu_max, expected
):
    _, set_cpu_max = cgroup_fs
    set_cpu_max("kubepods/pod/ctr", cpu_max)
    _cpus(monkeypatch, affinity=affinity, host=24)

    assert onnx_threads.usable_cpu_count() == expected


def test_a_limited_worker_gets_one_thread_per_usable_cpu(cgroup_fs, monkeypatch):
    _, set_cpu_max = cgroup_fs
    set_cpu_max("kubepods/pod/ctr", "max 100000")
    monkeypatch.delenv(onnx_threads.INTRA_OP_THREADS_ENV, raising=False)
    _cpus(monkeypatch, affinity=6, host=24)

    options = onnx_threads.onnx_session_options()

    assert options is not None
    assert options.intra_op_num_threads == 6


def test_an_unlimited_worker_keeps_the_onnx_runtime_default(cgroup_fs, monkeypatch):
    _, set_cpu_max = cgroup_fs
    set_cpu_max("kubepods/pod/ctr", "max 100000")
    monkeypatch.delenv(onnx_threads.INTRA_OP_THREADS_ENV, raising=False)
    _cpus(monkeypatch, affinity=24, host=24)

    assert onnx_threads.onnx_session_options() is None


@pytest.mark.parametrize("value", ["8", "0"])
def test_the_environment_variable_overrides_the_limit(cgroup_fs, monkeypatch, value):
    """An explicit pool size would override ORT_INTRA_OP_NUM_THREADS, so none is set."""
    _, set_cpu_max = cgroup_fs
    set_cpu_max("kubepods/pod/ctr", "max 100000")
    monkeypatch.setenv(onnx_threads.INTRA_OP_THREADS_ENV, value)
    _cpus(monkeypatch, affinity=6, host=24)

    assert onnx_threads.onnx_session_options() is None


@pytest.mark.parametrize("on_gpu", [True, False], ids=["gpu", "cpu"])
def test_preparation_and_the_engine_both_load_with_the_sized_options(
    on_gpu, monkeypatch
):
    sized = object()
    passed: list[object] = []

    def record_load(model_id, **kwargs):
        passed.append(kwargs["sess_options"])
        return object()

    monkeypatch.setattr(onnx_asr, "load_model", record_load)
    monkeypatch.setattr(onnx_threads, "onnx_session_options", lambda: sized)
    monkeypatch.setattr(onnx_asr_engine, "onnx_session_options", lambda: sized)
    monkeypatch.setattr(onnx_providers, "gpu_is_present", lambda: on_gpu)
    monkeypatch.setattr(onnx_asr_engine, "gpu_is_present", lambda: on_gpu)
    monkeypatch.setattr(onnx_asr_engine, "verify_gpu_providers", lambda *a, **k: False)

    engine = CanaryEngine()
    preload_models._prepare_onnx_asr_model(
        engine._to_onnx_asr_id(engine.default_model_id)
    )
    engine._get_model({})

    assert passed == [sized, sized]
