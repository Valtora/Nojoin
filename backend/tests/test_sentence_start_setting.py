"""The ``sentence_start_correction`` switch (#813): install-wide, on by default,
and off gives what both passes gave before the correction existed."""

from __future__ import annotations

import asyncio
import copy

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

import backend.utils.transcript_utils as transcript_utils
from backend.tests.postgres_setup import fresh_schema
from backend.tests.test_install_wide_transcription_settings import (
    _call_settings,
    _config_file,
)
from backend.tests.test_install_wide_transcription_settings import (
    install_config as install_config,
)
from backend.tests.test_live_sentence_start import (
    P1,
    P2,
    _enable_speakers,
    _evidence,
    _request,
    _speakers,
    _words,
)
from backend.tests.test_live_sentence_start import (
    settling_engine as settling_engine,
)
from backend.tests.test_live_settling_publication import _seed
from backend.tests.test_sentence_start import OVERLAP, TOKENS, _lines, _whisper
from backend.tests.test_transcript_utils import FakeDiarization
from backend.utils.canonical_pipeline.live_labels import apply_live_speaker_labels
from backend.utils.canonical_pipeline.live_settling import publish_live_settling
from backend.utils.config_manager import ConfigManager, config_manager
from backend.utils.sentence_start_settings import SENTENCE_START_CORRECTION_KEY
from backend.utils.transcript_utils import combine_transcription_diarization

KEY = SENTENCE_START_CORRECTION_KEY


def test_the_switch_is_install_wide_admin_only_and_on_by_default(
    install_config: ConfigManager,
) -> None:
    status, body, _ = asyncio.run(_call_settings("admin", {}, "GET"))
    assert status == 200 and body[KEY] is True

    status, body, row = asyncio.run(_call_settings("admin", {}, "POST", {KEY: False}))
    assert status == 200
    assert _config_file(install_config)[KEY] is False and body[KEY] is False
    assert KEY not in row

    status, body, row = asyncio.run(_call_settings("user", {}, "POST", {KEY: True}))
    assert status == 200 and body[KEY] is False
    assert _config_file(install_config)[KEY] is False and KEY not in row


# --- off is the merge and the live passes without the correction -------------------
def test_off_gives_the_final_merge_without_the_correction(monkeypatch) -> None:
    def merge(config):
        return combine_transcription_diarization(
            copy.deepcopy(_whisper(TOKENS)), FakeDiarization(OVERLAP), config
        )

    on, off = merge({}), merge({KEY: False})
    monkeypatch.setattr(
        transcript_utils, "correct_word_assignments", lambda *args, **kwargs: None
    )
    without = merge({})

    assert off == without
    assert _lines(on) != _lines(without)


def _switch(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    settings = {"live_speaker_labels": "off", KEY: on}
    monkeypatch.setattr(
        config_manager, "get", lambda key, default=None: settings.get(key, default)
    )


def _published(engine: Engine, *, activity: bool, label_pass: bool) -> list:
    """Speakers after one publication, and then a label pass when asked; each
    is given the native activity when ``activity``. Each call is a fresh
    capture in a fresh schema."""
    fresh_schema(engine, "live_settling_test")
    recording_id = _seed(engine)
    evidence = _evidence()
    with Session(engine) as session:
        _enable_speakers(session, recording_id)
        publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(),
            decide_speaker=evidence.decide,
            activity=evidence.native_activity() if activity else None,
        )
        if label_pass:
            apply_live_speaker_labels(
                session,
                recording_id=recording_id,
                settled_ms=0,
                decide=evidence.decide,
                activity=evidence.native_activity() if activity else None,
            )
        session.commit()
        return _speakers(session, recording_id)


@pytest.mark.parametrize("label_pass", [False, True], ids=["publication", "label-pass"])
def test_off_gives_the_live_passes_without_the_correction(
    settling_engine: Engine, monkeypatch: pytest.MonkeyPatch, label_pass: bool
) -> None:
    _switch(monkeypatch, True)
    on = _published(settling_engine, activity=True, label_pass=label_pass)
    without = _published(settling_engine, activity=False, label_pass=label_pass)
    _switch(monkeypatch, False)
    off = _published(settling_engine, activity=True, label_pass=label_pass)

    assert off == without
    assert on == [(P1, "It is done."), (P2, "so um we go")] != without
