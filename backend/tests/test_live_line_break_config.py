"""Recording-owner line pauses survive the live worker's configuration adapters."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.core import db
from backend.processing import live_settling as worker
from backend.processing import live_transcribe
from backend.processing.processing_tuning import LINE_BREAK_PAUSE_KEY
from backend.tests.test_live_settling_worker import _step
from backend.tests.test_live_settling_worker import rolling_lane as rolling_lane
from backend.utils.canonical_pipeline import live_settling as publication
from backend.worker import tasks


def test_two_recording_owners_keep_distinct_pauses_through_live_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recordings = {42: SimpleNamespace(user_id=1), 43: SimpleNamespace(user_id=2)}
    users = {
        1: SimpleNamespace(settings={LINE_BREAK_PAUSE_KEY: 0.5}),
        2: SimpleNamespace(settings={LINE_BREAK_PAUSE_KEY: 2.0}),
    }

    class OwnerSession:
        def get(self, model: Any, key: int) -> Any:
            return recordings[key] if model.__name__ == "Recording" else users[key]

        def exec(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("Configuration resolver is stubbed")

        def close(self) -> None:
            pass

    monkeypatch.setattr(db, "get_sync_session", OwnerSession)
    monkeypatch.setattr(
        tasks,
        "resolve_llm_config",
        lambda _session, settings: SimpleNamespace(merged_config=dict(settings)),
    )
    monkeypatch.setattr(
        live_transcribe.config_manager,
        "get",
        lambda key, default=None: {LINE_BREAK_PAUSE_KEY: 1.5}.get(key, default),
    )
    base = live_transcribe._build_live_config()
    first = live_transcribe._resolve_live_engine_config(42, dict(base))
    second = live_transcribe._resolve_live_engine_config(43, dict(base))
    assert base[LINE_BREAK_PAUSE_KEY] == 1.5
    assert first[LINE_BREAK_PAUSE_KEY] == 0.5
    assert second[LINE_BREAK_PAUSE_KEY] == 2.0


@pytest.mark.parametrize("pause_s", [0.5, 2.0])
@pytest.mark.parametrize("silent", [False, True], ids=["decode", "expiry"])
def test_worker_passes_each_resolved_pause_through_snapshot_to_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rolling_lane: Any,
    pause_s: float,
    silent: bool,
) -> None:
    step = _step(tmp_path)
    if silent:
        step.vad_regions.clear()
    transcript = SimpleNamespace(settling_state={"policy": {"enabled": True}})
    session = SimpleNamespace(commit=lambda: None, close=lambda: None)
    monkeypatch.setattr(db, "get_sync_session", lambda: session)
    monkeypatch.setattr(
        worker, "load_capture_publication", lambda *_args: (transcript, None)
    )
    monkeypatch.setattr(worker, "publication_is_current", lambda *_args: True)
    monkeypatch.setattr(worker, "live_speaker_labels_requested", lambda: False)
    monkeypatch.setattr(worker, "_publish_step", rolling_lane.publisher)
    captured: list[tuple[str, publication.SettlingPass]] = []

    def publish(
        _session: Any, *, request: publication.SettlingPass, **_kwargs: Any
    ) -> Any:
        captured.append(("decode", request))
        return publication.SettlingPublicationResult(True)

    def expire(_session: Any, *, request: publication.SettlingPass) -> Any:
        captured.append(("expiry", request))
        return publication.SettlingPublicationResult(True)

    def decode(*_args: Any) -> tuple[dict[str, Any], str]:
        assert not silent, "A speech-free step must not decode"
        return {"asr_segments": []}, "cuda"

    monkeypatch.setattr(publication, "publish_live_settling", publish)
    monkeypatch.setattr(publication, "expire_live_settling", expire)
    monkeypatch.setattr(worker, "_decode_words", decode)
    result = worker.run_settling_step(step, {LINE_BREAK_PAUSE_KEY: pause_s})
    assert result.handled and result.accepted
    assert len(captured) == 1
    kind, request = captured[0]
    assert kind == ("expiry" if silent else "decode")
    assert request.line_break_pause_s == pause_s
    assert request.expected_token == step.expected_token
    assert request.source_revision == rolling_lane.snapshot.revision
    assert LINE_BREAK_PAUSE_KEY not in rolling_lane.snapshot.policy


def test_unmarked_recording_returns_to_existing_live_lane_before_reading_pause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = SimpleNamespace(transcript=SimpleNamespace(settling_state=None))
    session = SimpleNamespace(get=lambda *_args: recording, close=lambda: None)
    monkeypatch.setattr(db, "get_sync_session", lambda: session)
    monkeypatch.setattr(
        worker, "resolve_tuning", lambda *_args: pytest.fail("Unmarked pause read")
    )
    monkeypatch.setattr(
        worker, "_decode_words", lambda *_args: pytest.fail("Unmarked rolling decode")
    )
    result = worker.run_settling_step(_step(tmp_path), {LINE_BREAK_PAUSE_KEY: 0.5})
    assert result == worker.SettlingStepResult(False)
