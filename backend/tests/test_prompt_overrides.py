"""Prompt edits cross real authentication, config persistence and rendering."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.deps import get_db
from backend.api.v1.endpoints import prompts, settings, system
from backend.core.security import API_ACCESS_SCOPE, API_TOKEN_TYPE, create_access_token
from backend.models.revoked_jwt import RevokedJwt
from backend.models.user import User
from backend.prompts import REGISTRY, load_registry, render_prompt
from backend.tests.sqlite_schemas import USERS_SCHEMA
from backend.utils import config_manager as config_module
from backend.utils.config_manager import ConfigManager

PROMPT_ID = "shared.calendar_title"
OVERRIDE = "Custom café: ${title} $$ {literal}"
ENTRY_FIELDS = {
    "id",
    "feature",
    "placeholders",
    "required_literals",
    "default_text",
    "override_text",
    "status",
}


@dataclass
class PromptAPI:
    client: AsyncClient
    manager: ConfigManager
    session: AsyncSession
    users: dict[str, User]
    headers: dict[str, dict[str, str]]

    async def call(
        self,
        method: str,
        path: str = "/settings/prompts",
        *,
        caller: str | None = "admin",
        payload: Any = None,
    ) -> Response:
        return await self.client.request(
            method, path, headers=self.headers.get(caller or "", {}), json=payload
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def prompt_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[PromptAPI]:
    monkeypatch.setenv("NOJOIN_DEVELOPER_MODE", "true")
    manager = ConfigManager(config_path=str(tmp_path / "config.json"))
    manager.save_values({"operator_unknown_key": {"keep": True}})
    for module in (config_module, prompts, settings):
        monkeypatch.setattr(module, "config_manager", manager)
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.execute(text(USERS_SCHEMA))
        await connection.run_sync(RevokedJwt.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        users = {
            role: User(username=role, hashed_password="unused", role=role)
            for role in ("owner", "admin", "user")
        }
        users["superuser"] = User(
            username="superuser",
            hashed_password="unused",
            role="user",
            is_superuser=True,
        )
        session.add_all(users.values())
        await session.commit()
        headers = {
            name: {
                "Authorization": "Bearer "
                + create_access_token(
                    name,
                    token_type=API_TOKEN_TYPE,
                    scopes=[API_ACCESS_SCOPE],
                    token_version=0,
                )
            }
            for name in users
        }
        app = FastAPI()
        app.include_router(settings.router, prefix="/settings")
        app.include_router(system.router, prefix="/system")

        async def test_database() -> AsyncIterator[AsyncSession]:
            yield session

        app.dependency_overrides[get_db] = test_database
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield PromptAPI(client, manager, session, users, headers)
    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [False, True])
async def test_capabilities_are_authenticated_and_follow_only_environment(
    prompt_api: PromptAPI,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    prompt_api.manager.save_values({"developer_mode": not enabled})
    monkeypatch.setenv("NOJOIN_DEVELOPER_MODE", str(enabled).lower())
    response = await prompt_api.call("GET", "/system/capabilities", caller="user")
    assert response.status_code == 200
    assert response.json() == {"developer_mode": enabled}
    anonymous = await prompt_api.call("GET", "/system/capabilities", caller=None)
    assert anonymous.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize("caller", ["admin", "user", None])
async def test_disabled_gate_hides_catalogue_and_mutations_before_authentication(
    prompt_api: PromptAPI,
    monkeypatch: pytest.MonkeyPatch,
    caller: str | None,
) -> None:
    monkeypatch.delenv("NOJOIN_DEVELOPER_MODE")
    before = prompt_api.manager.read_file()
    for method, path in (
        ("GET", "/settings/prompts"),
        ("PUT", f"/settings/prompts/{PROMPT_ID}"),
        ("DELETE", f"/settings/prompts/{PROMPT_ID}"),
    ):
        response = await prompt_api.call(
            method, path, caller=caller, payload={"text": OVERRIDE}
        )
        assert response.status_code == 404
    assert prompt_api.manager.read_file() == before


@pytest.mark.anyio
@pytest.mark.parametrize(
    "caller,status",
    [
        ("owner", 200),
        ("admin", 200),
        ("superuser", 200),
        ("user", 403),
        (None, 401),
    ],
)
async def test_prompt_routes_enforce_administrator_roles(
    prompt_api: PromptAPI,
    caller: str | None,
    status: int,
) -> None:
    for method, path in (
        ("GET", "/settings/prompts"),
        ("PUT", f"/settings/prompts/{PROMPT_ID}"),
        ("DELETE", f"/settings/prompts/{PROMPT_ID}"),
    ):
        response = await prompt_api.call(
            method, path, caller=caller, payload={"text": OVERRIDE}
        )
        assert response.status_code == status


@pytest.mark.anyio
async def test_save_is_durable_and_changes_the_next_render_then_reset_restores_default(
    prompt_api: PromptAPI,
) -> None:
    default = load_registry()[PROMPT_ID].template
    catalogue = (await prompt_api.call("GET")).json()
    assert {entry["id"] for entry in catalogue} == REGISTRY.keys()
    entry = next(entry for entry in catalogue if entry["id"] == PROMPT_ID)
    assert set(entry) == ENTRY_FIELDS
    assert entry == {
        "id": PROMPT_ID,
        "feature": "shared",
        "placeholders": ["title"],
        "required_literals": [],
        "default_text": default,
        "override_text": None,
        "status": "default",
    }
    schema = next(
        entry for entry in catalogue if entry["id"] == "notes.intelligence_schema"
    )
    assert schema["required_literals"] == [
        '"speaker_mapping"',
        '"title"',
        '"notes_markdown"',
    ]
    prompt_api.manager.save_values(
        {"prompt_overrides": {"future.prompt": {"text": "keep"}}}
    )
    before = prompt_api.manager.read_file()
    response = await prompt_api.call(
        "PUT", f"/settings/prompts/{PROMPT_ID}", payload={"text": OVERRIDE}
    )
    assert response.status_code == 200
    assert response.json() == {
        **entry,
        "override_text": OVERRIDE,
        "status": "overridden",
    }
    fresh = ConfigManager(config_path=prompt_api.manager.config_path)
    saved = fresh.get("prompt_overrides")
    assert saved[PROMPT_ID] == {
        "text": OVERRIDE,
        "base_sha256": sha256(default.encode()).hexdigest(),
    }
    assert saved["future.prompt"] == {"text": "keep"}
    assert {
        key: value
        for key, value in fresh.read_file().items()
        if key != "prompt_overrides"
    } == {key: value for key, value in before.items() if key != "prompt_overrides"}
    assert (
        render_prompt(PROMPT_ID, title="${opaque}")
        == "Custom café: ${opaque} $ {literal}"
    )
    reset = await prompt_api.call("DELETE", f"/settings/prompts/{PROMPT_ID}")
    assert reset.status_code == 200
    assert reset.json() == entry
    assert PROMPT_ID not in prompt_api.manager.read_file()["prompt_overrides"]
    assert render_prompt(PROMPT_ID, title="restored") == default.replace(
        "${title}", "restored"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "prompt_id,payload,detail",
    [
        (PROMPT_ID, {"text": "missing variable"}, "title"),
        (PROMPT_ID, {"text": "${title} ${unknown}"}, "unknown"),
        (PROMPT_ID, {"text": "${title} ${"}, "syntax"),
        (PROMPT_ID, {"text": " "}, "empty"),
        ("notes.intelligence_schema", {"text": "{}"}, "speaker_mapping"),
        (PROMPT_ID, {"text": 123}, None),
        (PROMPT_ID, {"text": None}, None),
        (PROMPT_ID, {"text": OVERRIDE, "base_sha256": "client-supplied"}, None),
    ],
)
async def test_invalid_edits_are_rejected_without_changing_config(
    prompt_api: PromptAPI,
    prompt_id: str,
    payload: dict[str, Any],
    detail: str | None,
) -> None:
    before = prompt_api.manager.read_file()
    response = await prompt_api.call(
        "PUT", f"/settings/prompts/{prompt_id}", payload=payload
    )
    assert response.status_code == 422
    if detail is not None:
        assert prompt_id in response.json()["detail"]
        assert detail in response.json()["detail"]
    assert prompt_api.manager.read_file() == before


@pytest.mark.anyio
@pytest.mark.parametrize(
    "prompt_id,literal",
    [
        (prompt_id, literal)
        for prompt_id, spec in REGISTRY.items()
        for literal in spec.required_literals
    ],
)
async def test_every_parser_literal_is_protected_at_the_save_boundary(
    prompt_api: PromptAPI,
    prompt_id: str,
    literal: str,
) -> None:
    before = prompt_api.manager.read_file()
    edited = load_registry()[prompt_id].template.replace(literal, "")
    response = await prompt_api.call(
        "PUT", f"/settings/prompts/{prompt_id}", payload={"text": edited}
    )
    assert response.status_code == 422
    assert prompt_id in response.json()["detail"]
    assert "missing required literal" in response.json()["detail"]
    assert prompt_api.manager.read_file() == before


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["PUT", "DELETE"])
async def test_unknown_prompt_ids_cannot_be_persisted(
    prompt_api: PromptAPI, method: str
) -> None:
    before = prompt_api.manager.read_file()
    response = await prompt_api.call(
        method, "/settings/prompts/unknown.id", payload={"text": "unknown"}
    )
    assert response.status_code == 404
    assert prompt_api.manager.read_file() == before


@pytest.mark.anyio
@pytest.mark.parametrize(
    "stored,status,expected_text",
    [
        ({"text": OVERRIDE, "base_sha256": "0" * 64}, "default changed", OVERRIDE),
        (
            {"text": "no placeholder", "base_sha256": "0" * 64},
            "invalid",
            "no placeholder",
        ),
        ({"text": 42}, "invalid", None),
        ("broken entry", "invalid", None),
    ],
)
async def test_catalogue_reloads_out_of_band_changes_and_keeps_invalid_edits_reviewable(
    prompt_api: PromptAPI,
    monkeypatch: pytest.MonkeyPatch,
    stored: object,
    status: str,
    expected_text: str | None,
) -> None:
    other_process = ConfigManager(config_path=prompt_api.manager.config_path)
    other_process.save_values({"prompt_overrides": {PROMPT_ID: stored}})
    entry = next(
        entry
        for entry in (await prompt_api.call("GET")).json()
        if entry["id"] == PROMPT_ID
    )
    assert entry["status"] == status
    assert entry["override_text"] == expected_text
    expected = (
        "Custom café: example $ {literal}"
        if status == "default changed"
        else "Meeting title: example"
    )
    assert render_prompt(PROMPT_ID, title="example") == expected
    monkeypatch.setenv("NOJOIN_DEVELOPER_MODE", "false")
    assert render_prompt(PROMPT_ID, title="example") == "Meeting title: example"


@pytest.mark.anyio
async def test_ordinary_settings_never_expose_or_accept_prompt_storage(
    prompt_api: PromptAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = {PROMPT_ID: {"text": OVERRIDE, "base_sha256": "0" * 64}}
    prompt_api.manager.save_values({"prompt_overrides": storage})
    user = prompt_api.users["admin"]
    user.settings = {"prompt_overrides": {"malicious": "row value"}}
    await prompt_api.session.commit()
    for enabled in ("true", "false"):
        monkeypatch.setenv("NOJOIN_DEVELOPER_MODE", enabled)
        response = await prompt_api.call("GET", "/settings")
        assert response.status_code == 200
        assert "prompt_overrides" not in response.json()
    saved = await prompt_api.call(
        "POST", "/settings", payload={"theme": "light", "prompt_overrides": {}}
    )
    assert saved.status_code == 200
    assert "prompt_overrides" not in saved.json()
    assert "prompt_overrides" not in user.settings
    assert prompt_api.manager.read_file()["prompt_overrides"] == storage


@pytest.mark.anyio
@pytest.mark.parametrize("contents", ["{broken json", "[]"])
async def test_a_corrupt_config_is_reported_without_overwriting_it(
    prompt_api: PromptAPI,
    contents: str,
) -> None:
    path = Path(prompt_api.manager.config_path)
    path.write_text(contents, encoding="utf-8")
    response = await prompt_api.call(
        "PUT", f"/settings/prompts/{PROMPT_ID}", payload={"text": OVERRIDE}
    )
    assert response.status_code == 500
    assert response.json() == {"detail": "Could not save prompt overrides."}
    assert path.read_text(encoding="utf-8") == contents
