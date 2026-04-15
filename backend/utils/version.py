from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from backend.utils.path_manager import PathManager

BUILD_VERSION_ENV_VAR = "NOJOIN_SERVER_VERSION"
DEFAULT_VERSION = "0.0.0"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _normalise_version(value: str | None) -> str | None:
    if value is None:
        return None

    normalised = value.strip()
    if not normalised:
        return None

    if normalised.startswith("v"):
        normalised = normalised[1:].strip()

    return normalised or None


def _get_version_from_environment() -> str | None:
    return _normalise_version(os.environ.get(BUILD_VERSION_ENV_VAR))


def _candidate_version_paths() -> list[Path]:
    paths: list[Path] = [
        Path("/app/.build-version"),
        REPO_ROOT / ".build-version",
    ]

    try:
        executable_directory = PathManager().executable_directory
        paths.extend(
            [
                executable_directory / ".build-version",
                executable_directory / "docs" / "VERSION",
            ]
        )
    except Exception:
        pass

    paths.extend(
        [
            Path("/app/docs/VERSION"),
            REPO_ROOT / "docs" / "VERSION",
        ]
    )

    deduped_paths: list[Path] = []
    seen_paths: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        deduped_paths.append(path)

    return deduped_paths


def _get_version_from_files() -> str | None:
    for path in _candidate_version_paths():
        try:
            if not path.exists():
                continue

            version = _normalise_version(path.read_text(encoding="utf-8"))
            if version:
                return version
        except OSError:
            continue

    return None


@lru_cache(maxsize=1)
def get_installed_version() -> str:
    return _get_version_from_environment() or _get_version_from_files() or DEFAULT_VERSION


def reset_installed_version_cache() -> None:
    get_installed_version.cache_clear()