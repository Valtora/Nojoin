"""Where the live label state goes when a recording is deleted or discarded.

The state holds speaker centroids and turn embeddings, so it must not outlive
the capture, and the storage hygiene sweep must neither delete a live one nor
report it. Finalize, and a finalize that cannot build its master, are tested
with the other finalize paths in ``test_browser_flac_capture``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine

from backend.models.recording import RecordingStatus
from backend.processing.live_diarization.files import LIVE_LABEL_FILENAMES
from backend.tests.storage_hygiene_harness import (
    add_recording,
    make_layout,
    run,
    sqlite_engine,
    write_file,
)


def write_label_state(live_dir: Path) -> list[Path]:
    """Every label state file in ``live_dir``, as a capture leaves them."""
    live_dir.mkdir(parents=True, exist_ok=True)
    paths = [live_dir / name for name in LIVE_LABEL_FILENAMES]
    for path in paths:
        path.write_bytes(b"state")
    return paths


def test_deleting_or_discarding_a_recording_removes_the_label_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Discard and permanent delete both remove a recording's files here.
    from backend.utils import recording_storage

    monkeypatch.setattr(
        recording_storage,
        "recording_upload_temp_dir",
        lambda recording_id, create=False: tmp_path / "temp" / str(recording_id),
    )
    paths = write_label_state(tmp_path / "temp" / "7" / "live")
    other = write_label_state(tmp_path / "temp" / "8" / "live")

    recording_storage.delete_recording_artifacts(
        recording_id=7,
        audio_path=None,
        proxy_path=None,
        logger=logging.getLogger(__name__),
    )

    assert not any(path.exists() for path in paths)
    assert all(path.exists() for path in other)


@pytest.mark.parametrize("delete", [False, True])
def test_storage_hygiene_never_lists_a_capture_label_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delete: bool
) -> None:
    layout = make_layout(tmp_path, monkeypatch)
    engine: Engine = sqlite_engine(tmp_path / "hygiene.db")
    try:
        audio = write_file(layout.recordings / "capture.wav")
        recording_id = add_recording(engine, audio, status=RecordingStatus.UPLOADING)
        live_dir = layout.recordings / "temp" / str(recording_id) / "live"
        # Older than the sweep's grace, as in a long meeting.
        paths = [
            write_file(live_dir / name, size=1000) for name in LIVE_LABEL_FILENAMES
        ]

        report = run(engine, delete=delete, bulk=True)
    finally:
        engine.dispose()

    assert all(path.exists() for path in paths)
    assert sum(entry.scanned for entry in report.categories.values()) == 1
    assert sum(entry.unreferenced for entry in report.categories.values()) == 0


def test_removing_the_state_takes_every_file_a_save_can_leave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.processing.live_diarization import state
    from backend.processing.live_diarization.engine import LiveLabelConfig
    from backend.processing.live_diarization.files import (
        DISABLED_FILENAME,
        remove_live_label_state,
    )
    from backend.tests.live_label_fakes import SpeakerTrackModel, speaker_audio

    config = LiveLabelConfig()
    engine = state.new_engine(tmp_path, config)
    engine.feed(speaker_audio([(0, 12, 1)], 12), 0, SpeakerTrackModel())
    state.save_engine(tmp_path, engine)
    (tmp_path / DISABLED_FILENAME).touch()

    def die_before_rename(src, dst):
        raise OSError("worker killed")

    # A worker killed between writing the new state and renaming it.
    monkeypatch.setattr(state.os, "replace", die_before_rename)
    with pytest.raises(OSError):
        state.save_engine(tmp_path, engine)
    assert len(list(tmp_path.iterdir())) == 4

    remove_live_label_state(tmp_path)

    assert list(tmp_path.iterdir()) == []
