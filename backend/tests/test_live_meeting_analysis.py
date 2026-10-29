"""Live discussion generation owns its result independently of capture."""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session

import backend.worker.tasks.analytics_ai as analytics_ai
from backend.tests.test_meeting_analysis_task import (
    MODEL_RESPONSE,
    FakeBackend,
    _configured,
    _make_engine,
    _payload,
    _row,
    _run,
    _seed,
)


@pytest.mark.parametrize("error", [None, RuntimeError("provider failed")])
def test_live_projection_generation_preserves_capture_and_previous_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception | None
) -> None:
    engine = _make_engine(tmp_path, "live")
    previous = {"topics": [{"title": "Previous discussion"}], "event_watermark": 1}
    with engine.begin() as connection:
        _seed(connection, analytics_payload=json.dumps({"ai": previous}))
        connection.execute(text("DELETE FROM transcript_utterances"))
        connection.execute(
            text("UPDATE recordings SET status='PAUSED', processing_progress=42")
        )
        connection.execute(
            text(
                "UPDATE transcripts SET transcript_status='processing', segments=:segments"
            ),
            {
                "segments": json.dumps(
                    [
                        {
                            "start": 0,
                            "end": 6,
                            "speaker": "SPEAKER_00",
                            "text": "Let's take it to two customers first and see what breaks.",
                        }
                    ]
                )
            },
        )
    backend = FakeBackend(MODEL_RESPONSE, error=error)
    result = _run(engine, monkeypatch, config=_configured(), backend=backend)
    assert backend.calls == 1
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT status, processing_progress FROM recordings")
        ).one() == ("PAUSED", 42)
    payload = _payload(_row(engine)[2])
    if error:
        assert result["status"] == "error"
        assert payload["ai"] == previous
    else:
        assert result["status"] == "success"
        assert backend.last_request.allowlist.resolve("Priya Patel") == "rs:1"
        assert payload["ai_generation"]["generated_through_ms"] == 6000


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("same_request", [False, True])
def test_superseded_worker_cannot_replace_newer_result_or_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool, same_request: bool
) -> None:
    engine = _make_engine(tmp_path, "superseded")
    newer = {
        "ai": {"topics": [{"title": "Newest"}]},
        "ai_generation": {"request_id": "newer"},
    }
    with engine.begin() as connection:
        _seed(connection)

    class SupersededBackend(FakeBackend):
        def generate_meeting_analysis(self, request: Any, **kwargs: Any) -> Any:
            with engine.begin() as connection:
                if same_request:
                    current = connection.execute(
                        text("SELECT analytics_payload FROM transcripts")
                    ).scalar_one()
                    newer["ai_generation"] = _payload(current)["ai_generation"]
                connection.execute(
                    text(
                        "UPDATE transcripts SET analytics_payload=:payload, analytics_ai_status='completed'"
                    ),
                    {"payload": json.dumps(newer)},
                )
            if fails:
                raise RuntimeError("old request failed")
            return super().generate_meeting_analysis(request, **kwargs)

    _run(
        engine,
        monkeypatch,
        config=_configured(),
        backend=SupersededBackend(MODEL_RESPONSE),
    )
    status, message, payload, _ = _row(engine)
    assert status == "completed"
    assert message is None
    assert _payload(payload) == newer


@pytest.mark.parametrize("fails", [False, True])
def test_a_run_superseded_while_the_newer_one_still_generates_is_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    """Request A is with the provider when request B is accepted. A finishing
    while B is still generating must leave B's claim and the old result alone."""
    engine = _make_engine(tmp_path, "superseded-generating")
    previous = {"topics": [{"title": "Previous discussion"}], "event_watermark": 1}
    with engine.begin() as connection:
        _seed(connection, analytics_payload=json.dumps({"ai": previous}))

    class SupersededBackend(FakeBackend):
        def generate_meeting_analysis(self, request: Any, **kwargs: Any) -> Any:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE transcripts SET analytics_payload=:payload, "
                        "analytics_ai_status='generating'"
                    ),
                    {
                        "payload": json.dumps(
                            {"ai": previous, "ai_generation": {"request_id": "B"}}
                        )
                    },
                )
            if fails:
                raise RuntimeError("old request failed")
            return super().generate_meeting_analysis(request, **kwargs)

    _run(
        engine,
        monkeypatch,
        config=_configured(),
        backend=SupersededBackend(MODEL_RESPONSE),
    )
    status, message, payload, _ = _row(engine)
    assert (status, message) == ("generating", None)
    assert _payload(payload) == {"ai": previous, "ai_generation": {"request_id": "B"}}


def test_stale_queued_token_does_not_call_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _make_engine(tmp_path, "stale-token")
    with engine.begin() as connection:
        _seed(
            connection,
            analytics_payload=json.dumps({"ai_generation": {"request_id": "new"}}),
        )
    monkeypatch.setattr(analytics_ai, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(
        analytics_ai,
        "_resolve_backend",
        lambda *_: pytest.fail("Stale token reached provider"),
    )
    assert (
        analytics_ai.compute_meeting_analysis_task.run(10, request_id="old")["reason"]
        == "generation_superseded"
    )


def test_live_authoritative_empty_snapshot_preserves_previous_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _make_engine(tmp_path, "empty-live")
    previous = {"topics": [{"title": "Keep this"}]}
    with engine.begin() as connection:
        _seed(connection, analytics_payload=json.dumps({"ai": previous}))
        connection.execute(text("DELETE FROM transcript_utterances"))
        connection.execute(
            text("UPDATE transcripts SET settling_state=:state, segments=:segments"),
            {
                "state": json.dumps({"eligible": True, "policy": {"enabled": True}}),
                "segments": json.dumps(
                    [{"text": "Obsolete projection", "speaker": "SPEAKER_00"}]
                ),
            },
        )
    backend = FakeBackend(MODEL_RESPONSE)
    result = _run(engine, monkeypatch, config=_configured(), backend=backend)
    assert result["reason"] == "no_attributed_speech"
    assert backend.calls == 0
    assert _payload(_row(engine)[2])["ai"] == previous
