"""The images take the distribution's CA distrusts as well as its other fixes (#585).

A CA the distribution distrusts stays trusted in an image until its
ca-certificates package moves, and the pinned bases are republished late or,
for the PyTorch base, never. pip verifies PyPI against the OS trust store too,
so a builder stage that skips the upgrade installs every requirement over a
connection that still trusts the root.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = ("docker/Dockerfile.api", "docker/Dockerfile.worker")


def _stages(dockerfile: str) -> list[str]:
    """Each build stage's instructions, with comment lines left out."""
    lines = (REPO_ROOT / dockerfile).read_text(encoding="utf-8").splitlines()
    text = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    return re.split(r"^FROM\s", text, flags=re.MULTILINE)[1:]


@pytest.mark.parametrize("dockerfile", DOCKERFILES)
def test_every_stage_upgrades_os_packages_before_installing(dockerfile: str) -> None:
    stages = _stages(dockerfile)
    assert len(stages) == 2
    for stage in stages:
        name = stage.split("\n", 1)[0].split()[-1]
        upgrade = stage.find("apt-get upgrade -y")
        install = stage.find("apt-get install")
        assert upgrade != -1, f"{dockerfile} {name} stage never runs apt-get upgrade"
        assert install == -1 or upgrade < install, (
            f"{dockerfile} {name} stage installs packages before upgrading"
        )


def test_api_installs_its_requirements_with_the_hash_pinned_pip() -> None:
    builder = _stages("docker/Dockerfile.api")[0]
    pinned = builder.find("--require-hashes -r requirements/pip.txt")
    backend = builder.find("-r requirements/backend.txt")
    assert pinned != -1
    assert pinned < backend
    assert "pip uninstall -y pip" in builder[backend:]

    lines = (REPO_ROOT / "requirements" / "pip.txt").read_text(encoding="utf-8")
    entries = [line for line in lines.splitlines() if not line.startswith("#")]
    assert entries[0].startswith("pip==")
    assert any("--hash=sha256:" in line for line in entries)
