"""scripts/release.py removes its BuildKit builder once the last bake is done.

The builder runs a privileged container, so it should not outlive the build,
whether the run succeeds, fails a gate or is interrupted. Every command is
faked here; nothing reaches Docker.
"""

import argparse
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"
CONFIG = "sha256:" + "c" * 64


def _load_script():
    spec = importlib.util.spec_from_file_location("nojoin_release_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses looks the module up in sys.modules while building Release.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release_script = _load_script()


class FakeHost:
    """Answers the commands release.py runs and records the ones that matter."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.leftover = False
        self.rm_fails = False
        self.interrupt_on: str | None = None

    def run(self, command, *, cwd=None, env=None, log=None):
        if command[:2] == ["git", "log"]:
            return "1700000000\n"
        if command[:3] == ["docker", "buildx", "bake"]:
            event = "bake " + log.stem.removeprefix("bake-")
            self.events.append(event)
            if event == self.interrupt_on:
                raise KeyboardInterrupt
            return ""
        if command[:3] == ["docker", "buildx", "create"]:
            self.events.append("create")
            return ""
        if command[:3] == ["docker", "buildx", "rm"]:
            self.events.append("rm")
            return ""
        if command[:3] == ["docker", "image", "inspect"]:
            return CONFIG
        raise AssertionError(f"unexpected command {command}")

    def subprocess_run(self, command, **kwargs):
        if command[:3] == ["docker", "buildx", "inspect"]:
            return SimpleNamespace(returncode=0 if self.leftover else 1)
        if command[:3] == ["docker", "buildx", "rm"]:
            self.events.append("rm")
            if self.rm_fails:
                return SimpleNamespace(returncode=1, stdout="", stderr="rm refused")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command {command}")

    def step(self, name, error=None):
        def record(*args, **kwargs):
            self.events.append(name)
            if error:
                raise error

        return record


@pytest.fixture
def host(monkeypatch, tmp_path):
    fake = FakeHost()
    module = release_script

    def resolve_release(version, dry_run, ref):
        return module.Release(
            version=version, sha="a" * 40, previous_tag="v1.0.0", dry_run=dry_run
        )

    def prepare_worktree(release):
        pin = release.src / "docker" / "Dockerfile.buildkit"
        pin.parent.mkdir(parents=True)
        pin.write_text("FROM moby/buildkit:v0.0.0@sha256:" + "b" * 64 + "\n")

    monkeypatch.setattr(module, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(module, "run", fake.run)
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=fake.subprocess_run))
    monkeypatch.setattr(module, "resolve_release", resolve_release)
    monkeypatch.setattr(module, "check_registry_login", lambda: None)
    monkeypatch.setattr(module, "check_ci", lambda release: None)
    monkeypatch.setattr(module, "prepare_worktree", prepare_worktree)
    monkeypatch.setattr(module, "layout_config", lambda layout: CONFIG)
    monkeypatch.setattr(module, "scan_images", fake.step("scan"))
    monkeypatch.setattr(module, "smoke", fake.step("smoke"))
    monkeypatch.setattr(module, "check_cli_advisories", fake.step("advisories"))
    monkeypatch.setattr(module, "verify_pushed", fake.step("verify"))
    monkeypatch.setattr(module, "tag_and_publish", fake.step("tag"))
    return fake


def _release_flow(*, dry_run):
    args = argparse.Namespace(
        version="9.9.9", dry_run=dry_run, ref="origin/main", no_wait=False
    )
    return release_script.release_flow(args, [])


def _ordered(events):
    # The scan runs beside the load and smoke, so its place in the order varies.
    return [event for event in events if event != "scan"]


@pytest.mark.parametrize("rm_fails", [False, True])
def test_release_removes_builder_after_the_push_bake(host, capsys, rm_fails):
    host.rm_fails = rm_fails

    release = _release_flow(dry_run=False)

    assert _ordered(host.events) == [
        "create",
        "bake build",
        "bake load",
        "smoke",
        "advisories",
        "bake push",
        "rm",
        *["verify"] * len(release_script.TARGETS),
        "tag",
    ]
    assert "scan" in host.events
    assert not release.builder
    warned = "could not remove the nojoin-release builder" in capsys.readouterr().err
    assert warned == rm_fails


def test_dry_run_removes_builder_after_the_load_bake(host):
    _release_flow(dry_run=True)

    assert _ordered(host.events) == [
        "create",
        "bake build",
        "bake load",
        "rm",
        "smoke",
        "advisories",
    ]


def test_failed_gate_removes_builder_and_keeps_its_error(host, monkeypatch, capsys):
    error = release_script.ReleaseError("Trivy gate failed for api")
    monkeypatch.setattr(release_script, "scan_images", host.step("scan", error))
    host.rm_fails = True

    with pytest.raises(release_script.ReleaseError, match="Trivy gate failed"):
        _release_flow(dry_run=False)

    assert _ordered(host.events) == ["create", "bake build", "bake load", "smoke", "rm"]
    assert "could not remove" in capsys.readouterr().err


def test_interrupt_removes_builder(host):
    host.interrupt_on = "bake build"

    with pytest.raises(KeyboardInterrupt):
        _release_flow(dry_run=False)

    assert host.events == ["create", "bake build", "rm"]


def test_builder_left_by_an_earlier_run_is_replaced(host):
    host.leftover = True
    release = release_script.Release(
        version="9.9.9", sha="a" * 40, previous_tag=None, dry_run=True
    )
    release_script.prepare_worktree(release)

    release_script.ensure_builder(release)

    assert host.events == ["rm", "create"]
    assert release.builder
