"""The per-user daily budget for the AI analytics tier.

The tier spends the user's own provider quota, so an administrator caps how
many runs a user starts per UTC day. The cap is checked where a run is
queued: the tab's button (the API), and the post-processing follow-up for
owners who opted in to automatic analysis.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints.transcripts import routes_analytics
from backend.api.v1.endpoints.transcripts.router import router
from backend.models.ai_analysis import AiAnalysisRun
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.services import ai_analysis_budget as budget
from backend.tests.sqlite_schemas import (
    AI_ANALYSIS_RUNS_SCHEMA,
    RECORDINGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)
from backend.tests.test_install_wide_transcription_settings import (  # noqa: F401
    _call_settings,
    install_config,
)
from backend.utils.config_manager import ConfigManager
from backend.worker.tasks import followups

# --- resolution ---


def _user(limit: int | None = None, settings: dict | None = None) -> User:
    return User(
        id=1,
        username="alice",
        hashed_password="x",
        role="user",
        settings=settings or {},
        ai_analysis_daily_limit=limit,
    )


@pytest.mark.parametrize(
    ("install", "override", "expected"),
    [
        (None, None, None),
        (0, None, None),
        (5, None, 5),
        (5, 2, 2),
        # A per-user 0 is an explicit "no cap" over an install cap.
        (5, 0, None),
        ("5", None, None),
        (True, None, None),
    ],
)
def test_the_cap_is_the_override_else_the_install_default(
    monkeypatch, install, override, expected
):
    monkeypatch.setattr(
        budget.config_manager,
        "get",
        lambda key, default=None: (
            install if key == "ai_analysis_daily_limit" else default
        ),
    )
    assert budget.resolve_daily_limit(_user(limit=override)) == expected


def test_the_day_is_the_utc_day():
    start, resets_at = budget.day_window(datetime(2026, 10, 11, 17, 45, 9))
    assert start == datetime(2026, 10, 11)
    assert resets_at == datetime(2026, 10, 12)


def test_an_exhausted_budget_names_the_cap_and_the_reset():
    b = budget.AiAnalysisBudget(limit=2, used=2, resets_at=datetime(2026, 10, 12))
    assert b.exhausted and b.remaining == 0
    assert b.exhausted_message() == (
        "Your daily AI analysis budget of 2 runs is used up. It resets at 00:00 UTC."
    )
    assert b.as_dict()["resets_at"].endswith("+00:00")
    assert not budget.AiAnalysisBudget(
        limit=None, used=99, resets_at=datetime(2026, 10, 12)
    ).exhausted


# --- the API ---


@pytest.fixture
def api(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router, prefix="/transcripts")
    state = SimpleNamespace(user=None, dispatched=[], broker_up=True)
    app.dependency_overrides[get_db] = _db_override(maker)
    app.dependency_overrides[get_current_user] = lambda: state.user

    monkeypatch.setattr(
        routes_analytics,
        "_backfill_and_compute",
        lambda _s, _r: (
            1,
            {"speakers": [], "metrics": {}, "attribution_warning": None},
            {},
        ),
    )

    async def dispatch(name, *, args, context, kwargs=None):
        if not state.broker_up:
            return None
        state.dispatched.append((name, tuple(args)))
        return SimpleNamespace(id=f"task-{len(state.dispatched)}")

    monkeypatch.setattr(routes_analytics, "dispatch_task_best_effort", dispatch)
    monkeypatch.setattr(
        routes_analytics, "get_canonical_transcript_revision", lambda *_: 7
    )

    async def setup(
        *, install_limit, user_limit=None, status="PROCESSED", payload=None
    ):
        monkeypatch.setattr(
            budget.config_manager,
            "get",
            lambda key, default=None: (
                install_limit if key == "ai_analysis_daily_limit" else default
            ),
        )
        async with engine.begin() as conn:
            for ddl in (
                USERS_SCHEMA,
                RECORDINGS_SCHEMA,
                TRANSCRIPTS_SCHEMA,
                AI_ANALYSIS_RUNS_SCHEMA,
            ):
                await conn.execute(text(ddl))
        state.user = _user(limit=user_limit)
        async with maker() as session:
            session.add(state.user)
            recording = Recording(
                id=7,
                name="Standup",
                audio_path="/tmp/standup.wav",
                status=status,
                user_id=1,
                upload_progress=100,
                processing_progress=100,
            )
            session.add(recording)
            session.add(
                Transcript(
                    recording_id=7,
                    transcript_status="completed",
                    notes_status="pending",
                    analytics_payload=payload or {},
                )
            )
            await session.commit()
            await session.refresh(recording)
            return recording

    async def post(recording):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            return await client.post(
                f"/transcripts/{recording.public_id}/analytics/ai/generate"
            )

    async def get(recording):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            response = await client.get(f"/transcripts/{recording.public_id}/analytics")
        assert response.status_code == 200, response.text
        return response.json()

    async def runs():
        async with maker() as session:
            return list((await session.execute(select(AiAnalysisRun))).scalars())

    async def get_ai(recording):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            return await client.get(f"/transcripts/{recording.public_id}/analytics/ai")

    async def complete():
        # Budget tests request successive completed generations, rather than
        # queueing duplicate requests while the first one still owns the row.
        async with maker() as session:
            transcript = (await session.execute(select(Transcript))).scalar_one()
            transcript.analytics_ai_status = "completed"
            await session.commit()

    yield SimpleNamespace(
        setup=setup,
        post=post,
        get=get,
        get_ai=get_ai,
        runs=runs,
        complete=complete,
        state=state,
    )
    app.dependency_overrides.clear()


def _db_override(maker):
    async def override_db():
        async with maker() as session:
            yield session

    return override_db


def test_runs_are_refused_once_the_cap_is_reached(api):
    async def run():
        recording = await api.setup(install_limit=2)
        statuses = []
        for _ in range(2):
            statuses.append((await api.post(recording)).status_code)
            await api.complete()
        third = await api.post(recording)
        body = await api.get(recording)
        return statuses, third, body, await api.runs()

    statuses, third, body, runs = asyncio.run(run())

    assert statuses == [200, 200]
    assert third.status_code == 429
    assert "budget of 2 runs is used up" in third.json()["detail"]
    assert len(api.state.dispatched) == 2
    assert [run.source for run in runs] == ["user", "user"]
    assert body["ai_budget"]["limit"] == 2
    assert body["ai_budget"]["used"] == 2
    assert body["ai_budget"]["remaining"] == 0


def test_no_cap_means_no_refusal_and_no_limit_in_the_read(api):
    async def run():
        recording = await api.setup(install_limit=None)
        statuses = []
        for _ in range(3):
            statuses.append((await api.post(recording)).status_code)
            await api.complete()
        return statuses, await api.get(recording)

    statuses, body = asyncio.run(run())

    assert statuses == [200, 200, 200]
    assert body["ai_budget"] == {
        "limit": None,
        "used": 3,
        "remaining": None,
        "resets_at": body["ai_budget"]["resets_at"],
    }


def test_a_per_user_override_wins_over_the_install_cap(api):
    async def run():
        recording = await api.setup(install_limit=5, user_limit=1)
        first = (await api.post(recording)).status_code
        await api.complete()
        second = (await api.post(recording)).status_code
        return first, second

    assert asyncio.run(run()) == (200, 429)


def test_a_dispatch_that_fails_spends_nothing(api):
    async def run():
        recording = await api.setup(install_limit=1)
        api.state.broker_up = False
        failed = (await api.post(recording)).status_code
        api.state.broker_up = True
        retried = (await api.post(recording)).status_code
        return failed, retried, await api.runs()

    failed, retried, runs = asyncio.run(run())

    assert failed == 503
    assert retried == 200
    assert len(runs) == 1


@pytest.mark.parametrize("status", ["UPLOADING", "PAUSED", "QUEUED", "PROCESSING"])
def test_live_discussion_claim_is_independent_and_read_preserves_old_content(
    api, status
):
    previous = {"topics": [{"title": "Previous"}], "event_watermark": 3}

    async def run():
        recording = await api.setup(
            install_limit=5, status=status, payload={"ai": previous}
        )
        first = await api.post(recording)
        second = await api.post(recording)
        read = await api.get_ai(recording)
        return first, second, read, await api.runs()

    first, second, read, runs = asyncio.run(run())
    assert first.status_code == 200
    assert second.status_code == 409
    assert len(runs) == 1
    assert read.status_code == 200
    body = read.json()
    assert body["ai_status"] == "generating"
    assert body["ai"] == previous
    assert body["ai_stale"] is True
    assert body["ai_generation"] == first.json()["ai_generation"]


def test_discussion_read_never_runs_audio_backfill_or_dispatch(api, monkeypatch):
    async def run():
        recording = await api.setup(install_limit=1, status="PAUSED")

        def forbidden(*args, **kwargs):
            pytest.fail("Discussion read entered final analytics")

        for name in ("_backfill_and_compute", "dispatch_task_best_effort", "claim_run"):
            monkeypatch.setattr(routes_analytics, name, forbidden)
        response = await api.get_ai(recording)
        return response, await api.runs()

    response, runs = asyncio.run(run())
    assert response.status_code == 200
    assert response.json()["ai_status"] == "pending"
    assert runs == []


# --- the post-processing follow-up ---


@pytest.fixture
def sync_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.begin() as conn:
        for ddl in (USERS_SCHEMA, RECORDINGS_SCHEMA, AI_ANALYSIS_RUNS_SCHEMA):
            conn.execute(text(ddl))
    import backend.core.db as core_db

    monkeypatch.setattr(core_db, "get_sync_session", lambda: Session(engine))
    return engine


def _seed(engine, *, settings: dict, limit: int | None, prior_runs: int = 0):
    with Session(engine) as session:
        session.add(_user(limit=limit, settings=settings))
        session.add(
            Recording(
                id=7,
                name="Standup",
                audio_path="/tmp/standup.wav",
                status="PROCESSED",
                user_id=1,
                upload_progress=100,
                processing_progress=100,
            )
        )
        for _ in range(prior_runs):
            session.add(AiAnalysisRun(user_id=1, recording_id=7, source="user"))
        session.commit()


def _runs(engine):
    with Session(engine) as session:
        return list(session.exec(select(AiAnalysisRun)))


@pytest.fixture
def queued(monkeypatch):
    from backend.worker.tasks import compute_meeting_analysis_task

    calls: list[int] = []
    monkeypatch.setattr(
        compute_meeting_analysis_task, "delay", lambda rid: calls.append(rid)
    )
    return calls


def test_an_owner_who_opted_in_gets_their_meeting_analysed(sync_db, queued):
    _seed(sync_db, settings={"auto_analyse_meetings": True}, limit=None)

    assert followups.dispatch_meeting_analysis_if_opted_in(7) is True
    assert queued == [7]
    assert [run.source for run in _runs(sync_db)] == ["auto"]


def test_the_default_is_off(sync_db, queued, monkeypatch):
    monkeypatch.setattr(budget.config_manager, "get", lambda key, default=None: default)
    _seed(sync_db, settings={}, limit=None)

    assert followups.dispatch_meeting_analysis_if_opted_in(7) is False
    assert queued == [] and _runs(sync_db) == []


def test_an_opted_in_owner_at_the_cap_is_skipped(sync_db, queued):
    _seed(sync_db, settings={"auto_analyse_meetings": True}, limit=2, prior_runs=2)

    assert followups.dispatch_meeting_analysis_if_opted_in(7) is False
    assert queued == []
    assert len(_runs(sync_db)) == 2


def test_a_broken_lookup_never_raises_into_the_pipeline(queued, monkeypatch):
    import backend.core.db as core_db

    def boom():
        raise RuntimeError("database down")

    monkeypatch.setattr(core_db, "get_sync_session", boom)

    assert followups.dispatch_meeting_analysis_if_opted_in(7) is False
    assert queued == []


# --- the settings ---


def _config(manager: ConfigManager) -> dict:
    return json.loads(Path(manager.config_path).read_text(encoding="utf-8"))


def test_an_admin_sets_the_install_cap_and_a_user_cannot(install_config):  # noqa: F811
    status, body, row = asyncio.run(
        _call_settings("admin", {}, "POST", {"ai_analysis_daily_limit": 3})
    )
    assert status == 200
    assert _config(install_config)["ai_analysis_daily_limit"] == 3
    assert body["ai_analysis_daily_limit"] == 3
    assert "ai_analysis_daily_limit" not in row

    status, _, row = asyncio.run(
        _call_settings("user", {}, "POST", {"ai_analysis_daily_limit": 9})
    )
    assert status == 200
    assert _config(install_config)["ai_analysis_daily_limit"] == 3
    assert "ai_analysis_daily_limit" not in row


def test_a_negative_cap_is_refused(install_config):  # noqa: F811
    status, _, _ = asyncio.run(
        _call_settings("admin", {}, "POST", {"ai_analysis_daily_limit": -1})
    )
    assert status == 422


def test_automatic_analysis_is_a_per_user_choice(install_config):  # noqa: F811
    status, body, row = asyncio.run(
        _call_settings("user", {}, "POST", {"auto_analyse_meetings": True})
    )
    assert status == 200
    assert body["auto_analyse_meetings"] is True
    assert row["auto_analyse_meetings"] is True
    assert "auto_analyse_meetings" not in _config(install_config)


# --- the per-user override on the user record ---


class _AdminSession:
    def __init__(self, user):
        self.user = user
        self.committed = False

    async def get(self, _model, pk, **_options):
        return self.user if pk == self.user.id else None

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    def add(self, _obj):
        return None

    async def commit(self):
        self.committed = True

    async def refresh(self, _obj):
        return None


@pytest.mark.anyio
async def test_an_admin_sets_and_clears_a_users_cap():
    from backend.api.v1.endpoints import users as users_ep
    from backend.models.user import UserUpdate

    target = _user(limit=None)
    admin = SimpleNamespace(id=2, role="admin", is_superuser=True)

    await users_ep.update_user(
        db=_AdminSession(target),
        user_id=1,
        user_in=UserUpdate(ai_analysis_daily_limit=4),
        current_user=admin,
    )
    assert target.ai_analysis_daily_limit == 4

    # An explicit null clears the override; an absent field leaves it alone.
    await users_ep.update_user(
        db=_AdminSession(target),
        user_id=1,
        user_in=UserUpdate(is_active=True),
        current_user=admin,
    )
    assert target.ai_analysis_daily_limit == 4
    await users_ep.update_user(
        db=_AdminSession(target),
        user_id=1,
        user_in=UserUpdate.model_validate({"ai_analysis_daily_limit": None}),
        current_user=admin,
    )
    assert target.ai_analysis_daily_limit is None


def test_a_negative_per_user_cap_is_refused():
    from pydantic import ValidationError

    from backend.models.user import UserUpdate

    with pytest.raises(ValidationError):
        UserUpdate(ai_analysis_daily_limit=-1)
