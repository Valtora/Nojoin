"""Dependency upgrades must not silently reuse another native golden record."""

import json
from pathlib import Path

import pytest

from scripts.nemotron_compatibility import check_nemotron_compatibility

ROOT = Path(__file__).resolve().parents[2]
RECORDS = (
    "backend/processing/nemotron_assets.json",
    "backend/tests/fixtures/nemotron/manifest.json",
    "docs/evaluation/nemotron-710.json",
)


@pytest.fixture
def compatibility_tree(tmp_path: Path) -> Path:
    for relative in ("requirements/worker.txt", *RECORDS):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    return tmp_path


def test_transformers_bump_requires_evidence(compatibility_tree: Path) -> None:
    assert check_nemotron_compatibility(compatibility_tree) == []
    path = compatibility_tree / "requirements/worker.txt"
    path.write_text(
        path.read_text().replace("transformers==5.20.2", "transformers==5.20.3")
    )
    assert any(
        "pin disagrees" in error
        for error in check_nemotron_compatibility(compatibility_tree)
    )


@pytest.mark.parametrize("relative", RECORDS[1:])
def test_new_assets_require_fixture_and_comparison(
    compatibility_tree: Path, relative: str
) -> None:
    path = compatibility_tree / relative
    value = json.loads(path.read_text())
    value["asset_manifest_sha256"] = "0" * 64
    path.write_text(json.dumps(value))
    assert any(
        relative in error for error in check_nemotron_compatibility(compatibility_tree)
    )


def test_incomplete_manifest_cannot_pass(compatibility_tree: Path) -> None:
    path = compatibility_tree / RECORDS[0]
    value = json.loads(path.read_text())
    del value["files"][0]["sha256"]
    path.write_text(json.dumps(value))
    assert any(
        "incomplete" in error
        for error in check_nemotron_compatibility(compatibility_tree)
    )
