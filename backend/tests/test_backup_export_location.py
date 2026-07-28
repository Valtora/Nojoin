"""Where backup exports live, and what reclaims scratch.

A finished archive is the only backup file that has to pass between containers.
It used to be written to a named volume mounted over /tmp in all four services,
which made /tmp permanent and shared, so every temporary file any service leaked
accumulated there for the life of the deployment. Exports now travel through the
data directory that every service already mounts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.backup import runtime


class _StubPathManager:
    def __init__(self, data_dir: Path) -> None:
        self.user_data_directory = data_dir


def test_export_directory_defaults_inside_the_shared_data_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BACKUP_EXPORT_DIR", raising=False)

    resolved = runtime.backup_export_directory(_StubPathManager(tmp_path))

    assert resolved == tmp_path / "backups"


def test_export_directory_honours_an_explicit_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BACKUP_EXPORT_DIR", "/mnt/exports")

    resolved = runtime.backup_export_directory(_StubPathManager(tmp_path))

    assert resolved == Path("/mnt/exports")


def test_export_directory_ignores_a_blank_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unset compose variable arrives as an empty string, not as absent."""
    monkeypatch.setenv("BACKUP_EXPORT_DIR", "   ")

    resolved = runtime.backup_export_directory(_StubPathManager(tmp_path))

    assert resolved == tmp_path / "backups"
