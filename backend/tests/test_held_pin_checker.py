import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_held_pins.py"
SPEC = importlib.util.spec_from_file_location("check_held_pins", SCRIPT_PATH)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


def test_cpu_profile_torchaudio_build_pin_is_checked(tmp_path, monkeypatch):
    hold = checker.HOLDS[0]
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)

    requirements = "torch==2.11.0\ntorchaudio==2.11.0\n"
    for relative in hold.matched_files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(requirements, encoding="utf-8")

    profiles = {
        "docker-compose.cpu.yml": (
            '        TORCH_VERSION: "2.11.0"\n        TORCHAUDIO_VERSION: "2.10.0"\n'
        ),
        "docker-compose.cuda.yml": (
            "        TORCH_BASE_IMAGE: "
            "pytorch/pytorch:2.11.0-cuda12.6-runtime@sha256:deadbeef\n"
        ),
        "docker-compose.rocm.yml": (
            "        TORCH_BASE_IMAGE: "
            "rocm/pytorch:rocm7.14_ubuntu24.04_py3.12_"
            "pytorch_release_2.11.0@sha256:deadbeef\n"
        ),
    }
    for relative, content in profiles.items():
        (tmp_path / relative).write_text(content, encoding="utf-8")

    problems = checker.check_drift(hold)

    assert any(
        "docker-compose.cpu.yml" in problem and "torchaudio 2.10.0" in problem
        for problem in problems
    )


def test_profile_base_images_must_match_release_build_args(tmp_path, monkeypatch):
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    profiles = {
        "docker-compose.cpu.yml": (
            "        TORCH_BASE_IMAGE: ubuntu:24.04@sha256:cpu-digest\n"
            "        WORKER_INFERENCE_BACKEND: cpu\n"
            '        TORCH_VERSION: "2.11.0"\n'
            '        TORCHAUDIO_VERSION: "2.11.0"\n'
        ),
        "docker-compose.cuda.yml": (
            "        TORCH_BASE_IMAGE: "
            "pytorch/pytorch:2.11.0-cuda@sha256:cuda-digest\n"
            "        WORKER_INFERENCE_BACKEND: cuda\n"
        ),
        "docker-compose.rocm.yml": (
            "        TORCH_BASE_IMAGE: rocm/pytorch:rocm7.14_"
            "pytorch_release_2.11.0@sha256:rocm-digest\n"
            "        WORKER_INFERENCE_BACKEND: rocm\n"
        ),
    }
    for relative, content in profiles.items():
        (tmp_path / relative).write_text(content, encoding="utf-8")

    rocm_base = "rocm/pytorch:rocm7.14_pytorch_release_2.11.0@sha256:rocm-digest"
    release = f"""
          - service: worker-cpu
            build_args: |
              TORCH_BASE_IMAGE=ubuntu:24.04@sha256:stale-cpu-digest
              WORKER_INFERENCE_BACKEND=cpu
              TORCH_VERSION=2.11.0
              TORCHAUDIO_VERSION=2.11.0
          - service: worker-cuda
            build_args: |
              TORCH_BASE_IMAGE=pytorch/pytorch:2.11.0-cuda@sha256:cuda-digest
              WORKER_INFERENCE_BACKEND=cuda
          - service: worker-rocm
            build_args: |
              TORCH_BASE_IMAGE={rocm_base}
              WORKER_INFERENCE_BACKEND=rocm
    """
    release_path = tmp_path / ".github/workflows/release.yml"
    release_path.parent.mkdir(parents=True)
    release_path.write_text(release, encoding="utf-8")

    problems = checker.check_worker_profile_release_parity()

    assert len(problems) == 1
    assert "docker-compose.cpu.yml" in problems[0]
    assert "TORCH_BASE_IMAGE" in problems[0]
    assert "stale-cpu-digest" in problems[0]
