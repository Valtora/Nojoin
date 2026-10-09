"""Saving and reading the per-user processing tuning values."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.models.registry  # noqa: F401
from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import settings as settings_ep
from backend.models.user import User
from backend.processing.processing_tuning import TUNING_KEYS
from backend.tests.sqlite_schemas import USERS_SCHEMA


class _FakeConfigManager:
    """Stands in for the module-level config_manager singleton."""

    def __init__(self) -> None:
        self.config: dict = {}

    def get_all(self):
        return dict(self.config)

    def save_config(self, config_data):
        self.config = dict(config_data)

    def reload(self, *, force: bool = False):
        pass

    def validate_config_value(self, key, value):
        return True


@pytest.fixture(autouse=True)
def fake_config(monkeypatch):
    fake = _FakeConfigManager()
    monkeypatch.setattr(settings_ep, "config_manager", fake)
    return fake


async def _exchange(requests, *, stored=None, role="user", is_superuser=False):
    """Run (method, json) requests as one user; return responses and the row."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.execute(text(USERS_SCHEMA))
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = maker()
    session.add(
        User(
            username="member",
            hashed_password="x",
            role=role,
            is_superuser=is_superuser,
            settings=dict(stored or {}),
        )
    )
    await session.commit()
    user = (await session.execute(select(User))).scalars().one()

    app = FastAPI()
    app.include_router(settings_ep.router, prefix="/settings")

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    responses = []
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            for method, body in requests:
                if method == "GET":
                    responses.append(await client.get("/settings"))
                else:
                    responses.append(await client.post("/settings", json=body))
        await session.refresh(user)
        return responses, dict(user.settings or {})
    finally:
        await session.close()
        await engine.dispose()


def _run(requests, **kwargs):
    return asyncio.run(_exchange(requests, **kwargs))


def test_a_non_admin_can_store_a_value() -> None:
    (response,), row = _run([("POST", {"vad_threshold": 0.3})])

    assert response.status_code == 200
    assert row["vad_threshold"] == 0.3
    assert response.json()["vad_threshold"] == 0.3


@pytest.mark.parametrize(
    "body",
    [
        {"vad_threshold": 0.95},
        {"vad_threshold": True},
        {"phantom_max_segments": 2.5},
        {"phantom_merge_threshold": 0.04},
        # Too large to convert to a float: must still be a 422, not a 500.
        {"vad_threshold": 10**400},
    ],
)
def test_an_out_of_range_value_is_rejected(body) -> None:
    (response,), row = _run([("POST", body)], stored={"vad_threshold": 0.4})

    assert response.status_code == 422
    assert row == {"vad_threshold": 0.4}


def test_null_resets_a_value_to_inherit() -> None:
    (response,), row = _run(
        [("POST", {"vad_threshold": None})], stored={"vad_threshold": 0.3}
    )

    assert response.status_code == 200
    assert row["vad_threshold"] is None
    assert response.json()["vad_threshold"] is None


def test_a_phantom_floor_above_the_default_merge_is_rejected() -> None:
    # Merge unset: its effective value is the 0.60 default.
    (response,), row = _run([("POST", {"phantom_embedding_floor": 0.65})])

    assert response.status_code == 400
    assert "phantom_embedding_floor" not in row


def test_a_phantom_floor_is_accepted_with_a_higher_merge_threshold() -> None:
    (response,), row = _run(
        [
            (
                "POST",
                {"phantom_embedding_floor": 0.65, "phantom_merge_threshold": 0.8},
            )
        ]
    )

    assert response.status_code == 200
    assert row["phantom_embedding_floor"] == 0.65


def test_lowering_merge_below_a_stored_floor_is_rejected() -> None:
    (response,), row = _run(
        [("POST", {"phantom_merge_threshold": 0.5})],
        stored={"phantom_embedding_floor": 0.55, "phantom_merge_threshold": 0.8},
    )

    assert response.status_code == 400
    assert row["phantom_merge_threshold"] == 0.8


def test_a_merge_threshold_under_the_installs_floor_is_rejected(fake_config) -> None:
    """Processing reads the install's floor for a user who has not set one, so
    the save is checked against it, and the message says where it comes from."""
    fake_config.config = {"phantom_embedding_floor": 0.55}

    (response,), row = _run([("POST", {"phantom_merge_threshold": 0.5})])

    assert response.status_code == 400
    assert "phantom_embedding_floor to 0.55" in response.json()["detail"]
    assert "phantom_merge_threshold" not in row


def test_a_floor_under_the_installs_merge_threshold_is_accepted(fake_config) -> None:
    # Above the shipped 0.60 merge threshold, but below the install's 0.8.
    fake_config.config = {"phantom_merge_threshold": 0.8}

    (response,), row = _run([("POST", {"phantom_embedding_floor": 0.7})])

    assert response.status_code == 200
    assert row["phantom_embedding_floor"] == 0.7


def test_an_install_conflict_does_not_block_a_whole_page_save(fake_config) -> None:
    """The settings page always sends both phantom keys. When the user has set
    neither, a conflict lies in the install's values alone (processing ignores
    it), so it must not block their save."""
    fake_config.config = {
        "phantom_embedding_floor": 0.7,
        "phantom_merge_threshold": 0.6,
    }

    (response,), row = _run(
        [
            (
                "POST",
                {
                    "theme": "light",
                    "phantom_embedding_floor": None,
                    "phantom_merge_threshold": None,
                },
            )
        ]
    )

    assert response.status_code == 200
    assert row["theme"] == "light"


def test_get_reports_the_installs_pair_and_hides_a_value_it_overrides(
    fake_config,
) -> None:
    fake_config.config = {"phantom_embedding_floor": 0.55, "phantom_merge_threshold": 4}

    (response,), _row = _run([("GET", None)], stored={"phantom_merge_threshold": 0.5})

    payload = response.json()
    # The stored 0.5 is under the install's 0.55 floor, so processing falls
    # back for both; the page shows it as unset.
    assert payload["phantom_merge_threshold"] is None
    # The install's unusable 4 reads as unset too.
    assert payload["phantom_thresholds_install"] == {
        "phantom_embedding_floor": 0.55,
        "phantom_merge_threshold": None,
    }


@pytest.mark.parametrize("stored", [1.7, 10**400], ids=["out-of-range", "huge-int"])
def test_an_unusable_stored_value_reads_as_unset(stored) -> None:
    (response,), _row = _run([("GET", None)], stored={"vad_threshold": stored})

    assert response.status_code == 200
    assert response.json()["vad_threshold"] is None


def test_a_stored_conflicting_phantom_pair_reads_as_unset() -> None:
    (response,), _row = _run(
        [("GET", None)],
        stored={"phantom_embedding_floor": 0.9, "phantom_merge_threshold": 0.8},
    )

    payload = response.json()
    assert payload["phantom_embedding_floor"] is None
    assert payload["phantom_merge_threshold"] is None


def test_a_stored_conflict_does_not_block_unrelated_saves() -> None:
    (response,), row = _run(
        [("POST", {"theme": "light"})],
        stored={"phantom_embedding_floor": 0.9, "phantom_merge_threshold": 0.8},
    )

    assert response.status_code == 200
    assert row["theme"] == "light"


@pytest.mark.parametrize(
    ("install", "stored"),
    [
        ({}, {"vad_threshold": 1.7}),
        # The unusable floor reads as unset, so the install's 0.5 is the floor
        # in effect, and it is not below the stored 0.45 merge threshold.
        (
            {"phantom_embedding_floor": 0.5},
            {"phantom_embedding_floor": 1.7, "phantom_merge_threshold": 0.45},
        ),
    ],
    ids=["unusable-value", "unusable-half-beside-install-value"],
)
def test_saving_back_what_was_read_survives_an_unusable_stored_value(
    fake_config, install, stored
) -> None:
    """The settings page posts the whole object it read. That round trip must
    not fail over a stored value the user cannot see, and must not stamp
    defaults onto the row."""
    fake_config.config = dict(install)
    (read,), _row = _run([("GET", None)], stored=stored)

    (saved,), row = _run([("POST", read.json())], stored=stored)

    assert saved.status_code == 200, saved.text
    assert all(row[key] is None for key in TUNING_KEYS)


def test_install_config_can_set_a_default_the_worker_sees(tmp_path) -> None:
    """An operator's flat key in config.json survives loading and reaches
    processing for a user who has not set the value; the user's own value wins."""
    from backend.utils.config_manager import ConfigManager
    from backend.utils.llm_config import _merge_llm_config

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"vad_threshold": 0.4}))
    base_config = ConfigManager(config_path=str(config_path)).get_all()

    def _merged(user_settings: dict) -> dict:
        return _merge_llm_config(
            base_config=base_config,
            system_keys={},
            owner_settings=None,
            user_settings=user_settings,
        ).merged_config

    assert _merged({"vad_threshold": None})["vad_threshold"] == 0.4
    assert _merged({"vad_threshold": 0.3})["vad_threshold"] == 0.3
