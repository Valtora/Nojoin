"""Tests for the measured-overlap task, the re-measure sweep, and baselines.

The overlap detection model itself is validated offline against AMI ground
truth (docs/ANALYTICS_EVIDENCE.md); what is pinned here is each piece's
contract with the rest of the system: the overlap block merges into the
shared payload without clobbering other tiers and never strands a
"generating" status, the sweep queues only outdated payloads and respects its
bound, and baselines compare only figures the current method produced, for
people with enough history, scoped to the requesting user.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session

import backend.worker.tasks.analytics as analytics_task
import backend.worker.tasks.analytics_overlap as overlap_task
from backend.processing.delivery_descriptors import DELIVERY_METHOD_VERSION
from backend.services.meeting_analytics.baselines import compute_delivery_baselines
from backend.tests.sqlite_schemas import (
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _make_engine(tmp_path: Path, name: str) -> Any:
    engine = create_engine(f"sqlite:///{tmp_path / name}.sqlite", future=True)
    with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
        ):
            connection.execute(text(schema))
    return engine


def _seed_user(connection, user_id: int = 1, username: str = "alice") -> None:
    connection.execute(
        text(
            "INSERT INTO users (id, created_at, updated_at, username, "
            "hashed_password, is_active, is_superuser, force_password_change, "
            "role, token_version, has_seen_demo_recording, "
            "has_seen_companion_retirement_notice) VALUES "
            "(:id, :ts, :ts, :name, 'x', 1, 0, 0, 'user', 0, 0, 0)"
        ),
        {"id": user_id, "name": username, "ts": _now()},
    )


def _seed_recording(
    connection,
    *,
    recording_id: int,
    user_id: int = 1,
    audio_path: str = "",
    payload: dict | None = None,
) -> None:
    # A seeded payload implies a completed measurement; no payload means the
    # tier is still pending, which is the only other state these tests need.
    analytics_status = "completed" if payload is not None else "pending"
    connection.execute(
        text(
            "INSERT INTO recordings (id, created_at, updated_at, name, public_id, "
            "meeting_uid, audio_path, status, upload_progress, "
            "processing_progress, is_archived, is_deleted, user_id, "
            "duration_seconds) VALUES "
            "(:id, :ts, :ts, :name, :pid, :uid, :audio, 'PROCESSED', 100, 100, "
            "0, 0, :user, 60.0)"
        ),
        {
            "id": recording_id,
            "name": f"Meeting {recording_id}",
            "pid": f"rec-{recording_id}",
            "uid": f"uid-{recording_id}",
            "audio": audio_path,
            "user": user_id,
            "ts": _now(),
        },
    )
    connection.execute(
        text(
            "INSERT INTO transcripts (id, created_at, updated_at, recording_id, "
            "segments, notes_status, transcript_status, analytics_status, "
            "analytics_payload) VALUES "
            "(:id, :ts, :ts, :rec, '[]', 'completed', 'completed', :status, :payload)"
        ),
        {
            "id": recording_id,
            "rec": recording_id,
            "status": analytics_status,
            "payload": json.dumps(payload) if payload is not None else None,
            "ts": _now(),
        },
    )


def _seed_speaker(
    connection,
    *,
    speaker_id: int,
    recording_id: int,
    global_speaker_id: int | None,
) -> None:
    connection.execute(
        text(
            "INSERT INTO recording_speakers (id, created_at, updated_at, "
            "public_id, recording_id, diarization_label, global_speaker_id, "
            "speaker_status, speaker_kind, identity_locked) "
            "VALUES (:id, :ts, :ts, :pid, :rec, :label, :gsid, 'active', "
            "'diarized', 0)"
        ),
        {
            "id": speaker_id,
            "pid": f"sp-{speaker_id}",
            "rec": recording_id,
            "label": f"SPEAKER_{speaker_id:02d}",
            "gsid": global_speaker_id,
            "ts": _now(),
        },
    )


def _transcript_payload(engine, recording_id: int) -> dict:
    with engine.begin() as connection:
        raw = connection.execute(
            text("SELECT analytics_payload FROM transcripts WHERE recording_id = :rec"),
            {"rec": recording_id},
        ).scalar_one()
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


class TestOverlapTask:
    def _run(self, engine, monkeypatch) -> dict:
        monkeypatch.setattr(overlap_task, "get_sync_session", lambda: Session(engine))
        return overlap_task.compute_overlap_analytics_task.run(10)

    def test_writes_a_completed_block_and_preserves_other_tiers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audio = tmp_path / "meeting.wav"
        audio.write_bytes(b"not really audio")
        engine = _make_engine(tmp_path, "overlap-ok")
        with engine.begin() as connection:
            _seed_user(connection)
            _seed_recording(
                connection,
                recording_id=10,
                audio_path=str(audio),
                payload={"delivery": {"speakers": {}}, "method_version": 2},
            )

        measured = {
            "method_version": 1,
            "total_overlap_ms": 12_000,
            "overlap_share_of_audio": 0.05,
            "region_count": 3,
            "regions": [[0, 4000], [10_000, 14_000], [20_000, 24_000]],
            "regions_truncated": False,
            "duration_ms": 240_000,
        }
        monkeypatch.setattr(
            "backend.processing.audio_overlap.measure_audio_overlap",
            lambda path, token: dict(measured),
        )

        result = self._run(engine, monkeypatch)

        assert result["status"] == "success"
        payload = _transcript_payload(engine, 10)
        assert payload["audio_overlap"]["status"] == "completed"
        assert payload["audio_overlap"]["total_overlap_ms"] == 12_000
        # The merge must not clobber the delivery tier's keys.
        assert payload["delivery"] == {"speakers": {}}
        assert payload["method_version"] == 2

    def test_a_failure_leaves_an_error_block_not_a_stuck_generating(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audio = tmp_path / "meeting.wav"
        audio.write_bytes(b"not really audio")
        engine = _make_engine(tmp_path, "overlap-fail")
        with engine.begin() as connection:
            _seed_user(connection)
            _seed_recording(connection, recording_id=10, audio_path=str(audio))

        def _boom(path, token):
            raise RuntimeError("model exploded")

        monkeypatch.setattr(
            "backend.processing.audio_overlap.measure_audio_overlap", _boom
        )

        result = self._run(engine, monkeypatch)

        assert result["status"] == "error"
        payload = _transcript_payload(engine, 10)
        assert payload["audio_overlap"]["status"] == "error"
        assert "could not be measured" in payload["audio_overlap"]["error_message"]

    def test_missing_audio_is_terminal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _make_engine(tmp_path, "overlap-noaudio")
        with engine.begin() as connection:
            _seed_user(connection)
            _seed_recording(
                connection, recording_id=10, audio_path=str(tmp_path / "gone.wav")
            )

        result = self._run(engine, monkeypatch)

        assert result["reason"] == "audio_missing"
        payload = _transcript_payload(engine, 10)
        assert payload["audio_overlap"]["status"] == "error"


class TestRemeasureSweep:
    def test_queues_only_outdated_payloads_within_the_bound(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _make_engine(tmp_path, "sweep")
        with engine.begin() as connection:
            _seed_user(connection)
            # Current-version payload: must not be queued.
            _seed_recording(
                connection,
                recording_id=1,
                payload={"method_version": DELIVERY_METHOD_VERSION},
            )
            # Outdated and version-less payloads: both due a re-measure.
            _seed_recording(
                connection,
                recording_id=2,
                payload={"method_version": DELIVERY_METHOD_VERSION - 1},
            )
            _seed_recording(
                connection,
                recording_id=3,
                payload={},
            )
            # Never measured at all: the sweep is not a backfill.
            _seed_recording(connection, recording_id=4)

        monkeypatch.setattr(analytics_task, "get_sync_session", lambda: Session(engine))
        queued: list[int] = []
        monkeypatch.setattr(
            analytics_task.compute_delivery_analytics_task,
            "delay",
            lambda recording_id: queued.append(recording_id),
        )

        result = analytics_task.remeasure_outdated_delivery_task.run(limit=10)

        assert result["queued"] == 2
        assert sorted(queued) == [2, 3]

        queued.clear()
        result = analytics_task.remeasure_outdated_delivery_task.run(limit=1)
        assert result["queued"] == 1


class TestDeliveryBaselines:
    def _payload(self, speaker_id: int, wpm: int, spread: float) -> dict:
        return {
            "method_version": DELIVERY_METHOD_VERSION,
            "delivery": {
                "speakers": {
                    f"rs:{speaker_id}": {
                        "words_per_minute": wpm,
                        "pitch_spread_semitones": spread,
                        "pause_count": 6,
                        "speech_ms": 120_000,
                    }
                }
            },
        }

    def test_median_across_enough_meetings_scoped_to_the_user(
        self, tmp_path: Path
    ) -> None:
        engine = _make_engine(tmp_path, "baselines")
        with engine.begin() as connection:
            _seed_user(connection, user_id=1)
            _seed_user(connection, user_id=2, username="mallory")
            # The meeting being viewed.
            _seed_recording(connection, recording_id=10)
            _seed_speaker(
                connection, speaker_id=1, recording_id=10, global_speaker_id=77
            )
            # Three measured meetings for the same person.
            for index, wpm in enumerate((150, 170, 190), start=1):
                _seed_recording(
                    connection,
                    recording_id=20 + index,
                    payload=self._payload(100 + index, wpm, 4.0 + index),
                )
                _seed_speaker(
                    connection,
                    speaker_id=100 + index,
                    recording_id=20 + index,
                    global_speaker_id=77,
                )
            # Another user's meeting with the same person: never counted.
            _seed_recording(
                connection,
                recording_id=30,
                user_id=2,
                payload=self._payload(200, 300, 9.0),
            )
            _seed_speaker(
                connection, speaker_id=200, recording_id=30, global_speaker_id=77
            )

        with Session(engine) as session:
            baselines = compute_delivery_baselines(
                session,
                user_id=1,
                recording_id=10,
                speakers=[{"speaker_key": "rs:1", "global_speaker_id": 77}],
            )

        assert baselines["rs:1"]["meetings"] == 3
        assert baselines["rs:1"]["words_per_minute"] == 170
        assert baselines["rs:1"]["pitch_spread_semitones"] == 6.0
        assert baselines["rs:1"]["pauses_per_minute"] == 3.0

    def test_old_method_versions_and_thin_history_produce_no_baseline(
        self, tmp_path: Path
    ) -> None:
        engine = _make_engine(tmp_path, "baselines-thin")
        with engine.begin() as connection:
            _seed_user(connection)
            _seed_recording(connection, recording_id=10)
            _seed_speaker(
                connection, speaker_id=1, recording_id=10, global_speaker_id=77
            )
            # Two current meetings: below the minimum of three.
            for index in (1, 2):
                _seed_recording(
                    connection,
                    recording_id=20 + index,
                    payload=self._payload(100 + index, 170, 5.0),
                )
                _seed_speaker(
                    connection,
                    speaker_id=100 + index,
                    recording_id=20 + index,
                    global_speaker_id=77,
                )
            # A superseded-method payload: comparable-looking, not comparable.
            _seed_recording(
                connection,
                recording_id=25,
                payload={
                    **self._payload(103, 170, 5.0),
                    "method_version": DELIVERY_METHOD_VERSION - 1,
                },
            )
            _seed_speaker(
                connection, speaker_id=103, recording_id=25, global_speaker_id=77
            )

        with Session(engine) as session:
            baselines = compute_delivery_baselines(
                session,
                user_id=1,
                recording_id=10,
                speakers=[{"speaker_key": "rs:1", "global_speaker_id": 77}],
            )

        assert baselines == {}
