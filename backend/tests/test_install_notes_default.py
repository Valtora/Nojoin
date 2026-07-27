"""Setting a notes structure as the install default (issue #149).

The install default is unusual: it is the only notes setting that lives in
config.json rather than on the user row, and the UI renders it from the
server's template list rather than from the settings object. Both of those
made a failure silent, so these tests pin the write actually landing, a
failed write being reported, and a bad id being refused.
"""

from __future__ import annotations

import asyncio

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
from backend.models.notes_template import NotesTemplate, NotesTemplateScope
from backend.models.user import User
from backend.tests.sqlite_schemas import NOTES_TEMPLATES_SCHEMA, USERS_SCHEMA

SECTIONS = "## Summary\nWhat happened."
INSTALL_TEMPLATE_ID = 1
PERSONAL_TEMPLATE_ID = 2


class _FakeConfigManager:
    """Stands in for the module-level config_manager singleton."""

    def __init__(self, *, fail: bool = False):
        self.config: dict = {}
        self.fail = fail
        self.saves = 0
        self.reloads = 0

    def get_all(self):
        return dict(self.config)

    def save_config(self, config_data):
        self.saves += 1
        if self.fail:
            raise OSError("Read-only file system")
        self.config = dict(config_data)

    def reload(self):
        self.reloads += 1

    def validate_config_value(self, key, value):
        return True


@pytest.fixture()
def fake_config(monkeypatch):
    fake = _FakeConfigManager()
    monkeypatch.setattr(settings_ep, "config_manager", fake)
    return fake


async def _build_app(*, role: str = "owner", is_superuser: bool = True):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.execute(text(USERS_SCHEMA))
        await conn.execute(text(NOTES_TEMPLATES_SCHEMA))
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # One session for the whole request: the endpoint writes the current user
    # back, so the authenticated user has to be attached to the same session
    # the dependency hands out.
    session = maker()
    session.add(
        User(
            username="owner",
            hashed_password="x",
            role=role,
            is_superuser=is_superuser,
            settings={},
        )
    )
    session.add(
        NotesTemplate(
            name="Board pack",
            description="",
            sections=SECTIONS,
            scope=NotesTemplateScope.INSTALL.value,
            user_id=None,
        )
    )
    session.add(
        NotesTemplate(
            name="My own",
            description="",
            sections=SECTIONS,
            scope=NotesTemplateScope.PERSONAL.value,
            user_id=1,
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
    return engine, app, session


async def _post_install_default(value, *, role="owner", is_superuser=True):
    engine, app, session = await _build_app(role=role, is_superuser=is_superuser)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            return await client.post(
                "/settings", json={"install_notes_template_id": value}
            )
    finally:
        await session.close()
        await engine.dispose()


def test_install_default_is_written_to_the_install_config(fake_config):
    response = asyncio.run(_post_install_default(INSTALL_TEMPLATE_ID))

    assert response.status_code == 200
    assert fake_config.config["install_notes_template_id"] == INSTALL_TEMPLATE_ID
    # Reloaded before the read-modify-write too, so a config edited out of band
    # is not reverted by this process's stale in-memory copy.
    assert fake_config.reloads >= 2


def test_a_failed_config_write_is_reported_rather_than_swallowed(monkeypatch):
    monkeypatch.setattr(settings_ep, "config_manager", _FakeConfigManager(fail=True))

    response = asyncio.run(_post_install_default(INSTALL_TEMPLATE_ID))

    assert response.status_code == 500
    assert "could not be written" in response.json()["detail"]


def test_a_personal_structure_cannot_become_the_install_default(fake_config):
    response = asyncio.run(_post_install_default(PERSONAL_TEMPLATE_ID))

    assert response.status_code == 400
    assert "install_notes_template_id" not in fake_config.config


def test_an_unknown_structure_cannot_become_the_install_default(fake_config):
    response = asyncio.run(_post_install_default(9999))

    assert response.status_code == 400
    assert "install_notes_template_id" not in fake_config.config


def test_clearing_the_install_default_is_allowed(fake_config):
    fake_config.config["install_notes_template_id"] = INSTALL_TEMPLATE_ID

    response = asyncio.run(_post_install_default(None))

    assert response.status_code == 200
    assert fake_config.config["install_notes_template_id"] is None


def test_a_non_admin_write_is_dropped_before_it_reaches_the_config(fake_config):
    response = asyncio.run(
        _post_install_default(INSTALL_TEMPLATE_ID, role="user", is_superuser=False)
    )

    assert response.status_code == 200
    assert fake_config.config == {}
