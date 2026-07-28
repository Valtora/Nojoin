"""Reclaiming pipeline scratch a killed worker left behind.

The finalise pipeline removes its own intermediates in a finally block, so nothing
accumulates on any normal or failing run. A worker killed outright never reaches
it. This used to be permanent, because /tmp was a named volume shared by every
service; it is now a private container directory, and the daily task sweeps it.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from backend.processing.audio_preprocessing import cleanup_stale_pipeline_temp_files


def _age(path: Path, hours: float) -> None:
    stamp = time.time() - (hours * 60 * 60)
    os.utime(path, (stamp, stamp))


def test_pipeline_sweep_reclaims_scratch_a_killed_worker_left_behind(
    tmp_path: Path,
) -> None:
    for name in (
        "tmpabc123_vad.wav",
        "tmpabc123_vad_processed.wav",
        "tmpabc123_vad_processed.mp3",
        "tmpdef456_preprocessed.wav",
    ):
        target = tmp_path / name
        target.write_bytes(b"audio")
        _age(target, 48)

    reclaimed = cleanup_stale_pipeline_temp_files(
        max_age_hours=24, temp_dir=str(tmp_path)
    )

    assert reclaimed == 4
    assert list(tmp_path.iterdir()) == []


def test_pipeline_sweep_keeps_scratch_from_a_run_still_in_flight(
    tmp_path: Path,
) -> None:
    recent = tmp_path / "tmpabc123_vad.wav"
    recent.write_bytes(b"audio")

    reclaimed = cleanup_stale_pipeline_temp_files(
        max_age_hours=24, temp_dir=str(tmp_path)
    )

    assert reclaimed == 0
    assert recent.exists()


def test_pipeline_sweep_matches_by_suffix_not_by_a_bare_temp_glob(
    tmp_path: Path,
) -> None:
    """The sweep runs over a shared temp directory, so it must not be greedy."""
    unrelated = [
        tmp_path / "tmpsomething.zip",
        tmp_path / "tmpsomething.wav",
        tmp_path / "important.wav",
        tmp_path / "vad.wav.keep",
    ]
    for target in unrelated:
        target.write_bytes(b"data")
        _age(target, 48)

    reclaimed = cleanup_stale_pipeline_temp_files(
        max_age_hours=24, temp_dir=str(tmp_path)
    )

    assert reclaimed == 0
    assert all(target.exists() for target in unrelated)


def test_pipeline_sweep_leaves_directories_alone(tmp_path: Path) -> None:
    directory = tmp_path / "stale_vad.wav"
    directory.mkdir()
    _age(directory, 48)

    reclaimed = cleanup_stale_pipeline_temp_files(
        max_age_hours=24, temp_dir=str(tmp_path)
    )

    assert reclaimed == 0
    assert directory.is_dir()


def test_pipeline_sweep_survives_an_unreadable_directory(tmp_path: Path) -> None:
    reclaimed = cleanup_stale_pipeline_temp_files(
        max_age_hours=24, temp_dir=str(tmp_path / "does-not-exist")
    )

    assert reclaimed == 0
