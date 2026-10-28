"""Publication regression for Canary alignment failure over locked lead-up."""

from dataclasses import replace

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from backend.tests.test_live_settling_publication import (
    _active,
    _request,
    _seed,
    settle_with_k,
)
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline.live_settling import publish_live_settling
from backend.utils.canonical_pipeline.live_settling_projection import row_words


def test_alignment_failure_does_not_repeat_locked_decode_context(
    settling_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settle_with_k(monkeypatch, 3)
    recording_id = _seed(settling_engine)
    words = [
        {"word": "Hello", "start_ms": 100, "end_ms": 400},
        {"word": " world", "start_ms": 550, "end_ms": 850},
    ]
    with Session(settling_engine) as session:
        for step in range(4):
            publish_live_settling(
                session,
                request=_request(session, recording_id, step, end_ms=(step + 1) * 2000),
                words=words,
            )
            session.commit()
        assert all(
            word["locked"] for word in row_words(_active(session, recording_id)[0])
        )
        publish_live_settling(
            session,
            request=replace(
                _request(session, recording_id, 4, end_ms=10000),
                input_start_ms=0,
                metadata={"alignment_error": "unavailable"},
            ),
            words=[{"word": "Hello world again", "start_ms": None, "end_ms": None}],
        )
        session.commit()
        actual = " ".join(row.text for row in _active(session, recording_id))
        assert actual == "Hello world again"
