"""A Discard that lands while a live run is in flight (issue #869).

Discard deletes the recording row and then removes the capture's temporary
folder, so the run fails to write its carry and its state. That is expected and
logs at INFO. A run that fails while its recording still exists is a real
failure and still logs ERROR.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
import torch

from backend.processing import live_run_failure
from backend.processing import live_transcribe as lt
from backend.processing import transcribe as transcribe_module
from backend.tests.test_live_transcription import _gap_lane, _run_live_task

LIVE_LOGGER = "backend.processing.live_transcribe"


def _discard_during_inference(monkeypatch, temp_dir, session, *, delete_row=True):
    """Make the engine's call stand in for a Discard sent while it runs."""

    def transcribe(path, config=None):
        if delete_row:
            session._recording = None
        shutil.rmtree(temp_dir)
        return {
            "text": "hello",
            "segments": [{"start": 0.0, "end": 1.0e9, "text": "hello"}],
        }

    monkeypatch.setattr(transcribe_module, "transcribe_audio", transcribe)


def _errors(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == LIVE_LOGGER and record.levelno >= logging.ERROR
    ]


def test_a_discard_during_a_live_run_stops_it_quietly(monkeypatch, tmp_path, caplog):
    temp_dir, _, transcript, session = _gap_lane(monkeypatch, tmp_path, 71, (0,))
    _discard_during_inference(monkeypatch, temp_dir, session)

    with caplog.at_level(logging.INFO, logger=LIVE_LOGGER):
        _run_live_task(monkeypatch, 71, 0, session)

    assert _errors(caplog) == []
    assert not any(record.exc_info for record in caplog.records)
    assert any(
        "the recording was discarded" in record.getMessage()
        and record.levelno == logging.INFO
        for record in caplog.records
    )
    assert transcript.segments == []
    # Nothing writes the discarded capture's folder back.
    assert not temp_dir.exists()


def test_a_live_run_failing_while_its_recording_exists_still_logs_errors(
    monkeypatch, tmp_path, caplog
):
    """Lost files under a recording that is still capturing are a real fault."""
    temp_dir, _, _, session = _gap_lane(monkeypatch, tmp_path, 72, (0,))
    _discard_during_inference(monkeypatch, temp_dir, session, delete_row=False)

    with caplog.at_level(logging.INFO, logger=LIVE_LOGGER):
        _run_live_task(monkeypatch, 72, 0, session)

    messages = [record.getMessage() for record in _errors(caplog)]
    assert any(message.startswith("Live transcription failed") for message in messages)
    assert any(
        message.startswith("Could not save the live state") for message in messages
    )


def test_a_discard_between_the_failure_and_its_state_write_is_quiet(
    monkeypatch, tmp_path, caplog
):
    """The run failed for its own reason, then the Discard removed its folder
    before the failed run's state was saved: only the run's failure is an error."""
    temp_dir, _, _, session = _gap_lane(monkeypatch, tmp_path, 73, (0,))

    def engine_fails(path, config=None):
        raise RuntimeError("engine exploded")

    def discard_then_write(live_dir, state):
        session._recording = None
        shutil.rmtree(temp_dir)
        raise FileNotFoundError(str(live_dir / "state.json"))

    monkeypatch.setattr(transcribe_module, "transcribe_audio", engine_fails)
    monkeypatch.setattr(lt, "write_live_state", discard_then_write)

    with caplog.at_level(logging.INFO, logger=LIVE_LOGGER):
        _run_live_task(monkeypatch, 73, 0, session)

    messages = [record.getMessage() for record in _errors(caplog)]
    assert len(messages) == 1
    assert messages[0].startswith("Live transcription failed")
    assert any(
        "the recording was discarded" in record.getMessage()
        for record in caplog.records
    )


def test_a_database_error_counts_as_the_recording_being_there(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from backend.core import db as db_module

    class _Unreachable:
        def get(self, model, recording_id):
            raise OperationalError("select", {}, Exception("connection refused"))

        def close(self):
            pass

    monkeypatch.setattr(db_module, "get_sync_session", _Unreachable)

    assert live_run_failure.capture_discarded(74) is False


@pytest.mark.parametrize("present", [True, False])
def test_capture_discarded_reads_the_recording_row(monkeypatch, present):
    from backend.core import db as db_module

    class _Session:
        def get(self, model, recording_id):
            return object() if present else None

        def close(self):
            pass

    monkeypatch.setattr(db_module, "get_sync_session", _Session)

    assert live_run_failure.capture_discarded(75) is (not present)


def _loud(caplog) -> list[logging.LogRecord]:
    """Errors from any logger, and warnings that carry a traceback."""
    return [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR
        or (record.levelno >= logging.WARNING and record.exc_info)
    ]


def _engine_that_discards(discard) -> SimpleNamespace:
    """An engine whose call is where the Discard lands. It then reads its input
    file, as every real engine does, so an input in the capture's folder fails."""

    def transcribe(path, config):
        discard()
        Path(path).read_bytes()
        return {
            "text": "hello",
            "language": "en",
            "segments": [
                {
                    "start": 0.0,
                    "end": 0.5,
                    "text": "hello",
                    "words": [{"word": "hello", "start": 0.0, "end": 0.5}],
                }
            ],
        }

    return SimpleNamespace(
        name="whisper",
        transcribe=transcribe,
        loaded_device=lambda _config: "cuda",
        release=lambda: None,
    )


def _register_engine(monkeypatch, engine) -> None:
    """Every backend name dispatches to ``engine`` through the real dispatcher."""
    for name in ("whisper", "parakeet", "canary"):
        monkeypatch.setitem(transcribe_module._ENGINE_REGISTRY, name, engine)
    monkeypatch.setattr(
        transcribe_module, "engine_has_native_word_timestamps", lambda _b: True
    )


def _private_tempdir(monkeypatch, path: Path) -> Path:
    path.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(path))
    return path


def test_a_discard_during_the_engine_call_logs_nothing_loud(
    monkeypatch, tmp_path, caplog
):
    """Through the real dispatcher: the engine's input is not in the folder the
    Discard removes, so the call succeeds and the run then stops quietly."""
    dispatcher = transcribe_module.transcribe_audio
    temp_dir, _, transcript, session = _gap_lane(monkeypatch, tmp_path, 76, (0,))
    monkeypatch.setattr(transcribe_module, "transcribe_audio", dispatcher)
    private = _private_tempdir(monkeypatch, tmp_path / "private")

    def discard():
        session._recording = None
        shutil.rmtree(temp_dir, ignore_errors=True)

    _register_engine(monkeypatch, _engine_that_discards(discard))
    caplog.set_level(logging.INFO)

    _run_live_task(monkeypatch, 76, 0, session)

    assert _loud(caplog) == []
    assert any(
        "the recording was discarded" in record.getMessage()
        for record in caplog.records
    )
    assert transcript.segments == []
    assert not temp_dir.exists()
    assert list(private.iterdir()) == []


def test_a_discard_during_a_settling_decode_logs_nothing_loud(
    monkeypatch, tmp_path, caplog
):
    from backend.processing import live_settling
    from backend.processing.vad import silero_vad
    from backend.tests.test_live_settling_worker import _step

    capture = tmp_path / "capture"
    capture.mkdir()
    step = replace(_step(capture, sequence=0), audio_end_ms=2000)
    private = _private_tempdir(monkeypatch, tmp_path / "private")
    monkeypatch.setattr(
        silero_vad,
        "save_audio",
        lambda path, audio, sampling_rate: sf.write(
            path, audio.squeeze(0).numpy(), sampling_rate
        ),
    )
    _register_engine(
        monkeypatch,
        _engine_that_discards(lambda: shutil.rmtree(capture, ignore_errors=True)),
    )
    caplog.set_level(logging.INFO)

    payload, device = live_settling._decode_words(
        step,
        {"transcription_backend": "whisper"},
        torch.from_numpy(np.ones(32000, np.float32) * 0.1),
        0,
    )

    assert device == "cuda"
    assert payload["asr_segments"][0]["words"][0]["word"] == "hello"
    assert _loud(caplog) == []
    assert not capture.exists()
    assert list(private.iterdir()) == []
