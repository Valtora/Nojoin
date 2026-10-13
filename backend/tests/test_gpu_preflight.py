from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.processing import gpu_preflight
from backend.processing.gpu_preflight import HIDE_EXIT_CODE, Device, decide

TURING = Device(0, "RTX 2080 SUPER", (7, 5), "GPU-aaaa")
BLACKWELL = Device(1, "RTX 5090", (12, 0), "GPU-bbbb")
PASCAL = Device(2, "Tesla P40", (6, 1), "GPU-cccc")


def test_supported_gpus_are_left_alone():
    decision = decide(True, [TURING, BLACKWELL], "595.99.02", "13.0")

    assert decision.visible is None
    assert decision.messages == ()


def test_pre_turing_gpu_alone_is_hidden_and_the_worker_uses_the_cpu():
    decision = decide(True, [PASCAL], "580.95.05", "13.0")

    assert decision.visible == ""
    assert "Tesla P40" in decision.messages[0]
    assert "6.1" in decision.messages[0]
    assert decision.messages[-1] == "Processing on this worker runs on the CPU."


def test_only_the_pre_turing_gpu_is_hidden_beside_a_supported_one():
    decision = decide(True, [TURING, PASCAL], "580.95.05", "13.0")

    assert decision.visible == "GPU-aaaa"
    assert len(decision.messages) == 1
    assert "Tesla P40" in decision.messages[0]


def test_driver_older_than_580_is_named_as_the_cause():
    decision = decide(False, [], "570.172.08", "13.0")

    assert decision.visible == ""
    assert "570.172.08 is older than 580" in decision.messages[0]


def test_unusable_gpu_with_a_new_driver_points_at_the_setup_not_the_driver():
    decision = decide(False, [], "595.99.02", "13.0")

    assert decision.visible == ""
    assert "older than" not in decision.messages[0]
    assert "NVIDIA Container Toolkit" in decision.messages[0]


@pytest.mark.parametrize(
    ("version", "major"),
    [("595.99.02", 595), ("580", 580), ("", None), (None, None), ("unknown", None)],
)
def test_driver_major(version, major):
    assert gpu_preflight.driver_major(version) == major


def test_driver_version_is_read_from_the_kernel_module(tmp_path, monkeypatch):
    proc = tmp_path / "version"
    proc.write_text(
        "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  595.99.02  "
        "Release Build  (dvs-builder@U22-I3-AF05-06-2)  Wed Aug 26 05:20:50 UTC 2026\n"
    )
    monkeypatch.setattr(gpu_preflight, "_DRIVER_VERSION_FILE", proc)

    assert gpu_preflight.read_driver_version() == "595.99.02"


def test_no_gpu_attached_exits_cleanly_without_output(monkeypatch, capsys):
    monkeypatch.setattr(gpu_preflight, "gpu_is_present", lambda: False)

    assert gpu_preflight.main() == 0
    assert capsys.readouterr() == ("", "")


def _fake_torch(capability: tuple[int, int]) -> SimpleNamespace:
    props = SimpleNamespace(
        name="Tesla P40", major=capability[0], minor=capability[1], uuid="cccc"
    )
    return SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            get_device_properties=lambda index: props,
        ),
        version=SimpleNamespace(cuda="13.0"),
    )


def test_main_prints_the_visible_devices_and_signals_the_entrypoint(
    monkeypatch, capsys
):
    monkeypatch.setattr(gpu_preflight, "gpu_is_present", lambda: True)
    monkeypatch.setattr(gpu_preflight, "read_driver_version", lambda: "580.95.05")
    monkeypatch.setitem(sys.modules, "torch", _fake_torch((6, 1)))

    assert gpu_preflight.main() == HIDE_EXIT_CODE
    out, err = capsys.readouterr()
    assert out == "\n"
    assert "[gpu-preflight] GPU 0 (Tesla P40)" in err


def test_main_is_silent_for_a_supported_gpu(monkeypatch, capsys):
    monkeypatch.setattr(gpu_preflight, "gpu_is_present", lambda: True)
    monkeypatch.setattr(gpu_preflight, "read_driver_version", lambda: "595.99.02")
    monkeypatch.setitem(sys.modules, "torch", _fake_torch((7, 5)))

    assert gpu_preflight.main() == 0
    assert capsys.readouterr() == ("", "")
