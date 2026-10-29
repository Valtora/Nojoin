"""Live publication asks for the title and gist once the delay has passed."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.worker.tasks as tasks_module
from backend.models.recording import RecordingStatus
from backend.processing import live_transcribe as lt
from backend.tests.test_live_transcription import (
    _FakeRecording,
    _FakeSession,
    _FakeTranscript,
    _make_segment_wav,
    _patch_live_deps,
    _run_live_task,
)


class _LiveCapture:
    """A real live lane over a fake database, starting ``start_s`` into capture."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        start_s: float,
        gist: str | None = None,
        broker_fails: bool = False,
    ) -> None:
        self.temp_dir = tmp_path / "24"
        (self.temp_dir / "live").mkdir(parents=True)
        lt.write_live_state(
            self.temp_dir / "live", {"next_expected": 0, "buffer_abs_start": start_s}
        )
        monkeypatch.setattr(
            lt, "recording_upload_temp_dir", lambda rid, create=False: self.temp_dir
        )
        self.audio_store = _patch_live_deps(
            monkeypatch, speech_map=lambda audio: [{"start": 0.0, "end": 1.0}]
        )
        self.transcript = _FakeTranscript()
        recording = _FakeRecording(RecordingStatus.UPLOADING, self.transcript)
        recording.user = SimpleNamespace(settings={"live_title_delay_minutes": 1})
        recording.gist = gist
        self.recording = recording
        self.session = _FakeSession(recording)
        self.calls: list[int] = []
        self.monkeypatch = monkeypatch

        def dispatch(recording_id: int) -> None:
            assert self.session.committed
            self.calls.append(recording_id)
            if broker_fails:
                raise RuntimeError("broker down")

        monkeypatch.setattr(
            tasks_module, "generate_live_title_task", SimpleNamespace(delay=dispatch)
        )

    def publish(self, sequence: int, seconds: float = 2.0) -> None:
        _make_segment_wav(self.temp_dir, sequence, seconds, self.audio_store)
        _run_live_task(self.monkeypatch, 24, sequence, self.session)


@pytest.mark.parametrize("start_s, dispatched", [(57.9, False), (58.0, True)])
def test_live_title_waits_for_the_delay_in_capture_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start_s: float, dispatched: bool
) -> None:
    capture = _LiveCapture(tmp_path, monkeypatch, start_s=start_s)
    capture.publish(0)
    assert capture.calls == ([24] if dispatched else [])
    assert len(capture.transcript.segments) == 1


@pytest.mark.parametrize("start_s, dispatched", [(297.9, False), (298.0, True)])
def test_live_title_is_on_after_five_minutes_for_an_owner_who_never_chose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start_s: float, dispatched: bool
) -> None:
    capture = _LiveCapture(tmp_path, monkeypatch, start_s=start_s)
    capture.recording.user.settings = {}
    capture.publish(0)
    assert capture.calls == ([24] if dispatched else [])


@pytest.mark.parametrize("existing_gist", [None, "Already generated."])
@pytest.mark.parametrize("broker_fails", [False, True])
def test_live_title_dispatch_happens_after_commit_and_is_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_gist: str | None,
    broker_fails: bool,
) -> None:
    capture = _LiveCapture(
        tmp_path,
        monkeypatch,
        start_s=60.0,
        gist=existing_gist,
        broker_fails=broker_fails,
    )
    capture.publish(0)
    assert capture.calls == ([24] if existing_gist is None else [])
    assert len(capture.transcript.segments) == 1
    assert lt.read_live_state(capture.temp_dir / "live")["next_expected"] == 1


def test_a_failed_live_title_is_retried_once_after_another_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The gist never arrives, as when every model call fails.
    capture = _LiveCapture(tmp_path, monkeypatch, start_s=60.0)
    capture.publish(0)
    assert capture.calls == [24]
    capture.publish(1)
    capture.publish(2)
    assert capture.calls == [24], "no retry on every publication"
    capture.publish(3, seconds=60.0)
    assert capture.calls == [24, 24], "one retry after another delay"
    capture.publish(4, seconds=60.0)
    capture.publish(5, seconds=60.0)
    assert capture.calls == [24, 24], "and no further retries"
