"""Each user accepts the legal disclaimer once (#1004).

The setup account is the only one never asked. Everyone else, invited or
created by an administrator, accepts from the web app, and until they do the
API refuses them a new capture or import. Everything else, a capture or import
that already exists included, stays open to them. Tokens and the user rows are
real here, so the refusal is tested through the same authentication a browser
and an API client go through.
"""

from __future__ import annotations

import ast
import io
import wave
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.api.deps import (
    LEGAL_ACCEPTANCE_REQUIRED_CODE,
    enforce_legal_acceptance,
    get_db,
)
from backend.api.v1.api import api_router
from backend.api.v1.endpoints.login import _build_login_metadata
from backend.core import security
from backend.models.user import User, UserRead, needs_legal_acceptance
from backend.tests.sqlite_schemas import USERS_SCHEMA
from backend.tests.test_recording_pause_resume import (  # noqa: F401 -- fixture
    TRUSTED_ORIGIN,
    test_session_maker,
)
from backend.utils.time import utc_now

REPO_ROOT = Path(__file__).resolve().parents[2]
# Naive UTC, as the schema stores every time.
ACCEPTED_AT = datetime(2026, 11, 2, 9, 30)
ORIGIN = {"Origin": TRUSTED_ORIGIN}

REVOKED_JWTS_SCHEMA = """
CREATE TABLE revoked_jwts (
    jti VARCHAR(64) PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_type VARCHAR(32) NOT NULL,
    expires_at DATETIME NOT NULL,
    revoked_at DATETIME NOT NULL,
    reason VARCHAR(64)
)
"""

# The made-up accounts every test starts with: the setup account of an install
# set up before acceptance was recorded, a user who has accepted, two who have
# not (one invited, one an administrator made) and one who must still change
# the temporary password an administrator gave them.
ACCOUNTS = {
    "owner": {"id": 1, "role": "owner", "is_superuser": True, "accepted": None},
    "alice": {"id": 2, "role": "user", "is_superuser": False, "accepted": None},
    "bob": {"id": 3, "role": "user", "is_superuser": False, "accepted": ACCEPTED_AT},
    "carol": {"id": 4, "role": "admin", "is_superuser": False, "accepted": None},
    "dave": {
        "id": 5,
        "role": "user",
        "is_superuser": False,
        "accepted": None,
        "force_password_change": True,
    },
}


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


@pytest.fixture
async def session_maker(test_session_maker: sessionmaker) -> sessionmaker:  # noqa: F811
    engine = test_session_maker.kw["bind"]
    async with engine.begin() as connection:
        await connection.execute(text(USERS_SCHEMA))
        await connection.execute(text(REVOKED_JWTS_SCHEMA))
        for username, account in ACCOUNTS.items():
            await connection.execute(
                text(
                    "INSERT INTO users (id, created_at, updated_at, username,"
                    " hashed_password, is_active, is_superuser,"
                    " force_password_change, role, token_version, settings,"
                    " has_seen_demo_recording, legal_accepted_at) VALUES"
                    " (:id, :now, :now, :username, 'x', 1, :is_superuser,"
                    " :force, :role, 0, '{}', 1, :accepted)"
                ),
                {
                    "id": account["id"],
                    "now": datetime(2026, 11, 1),
                    "username": username,
                    "is_superuser": account["is_superuser"],
                    "force": account.get("force_password_change", False),
                    "role": account["role"],
                    "accepted": account["accepted"],
                },
            )
    return test_session_maker


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session_maker: sessionmaker,
    stub_celery_dispatch: list,
):
    monkeypatch.setenv("WEB_APP_URL", TRUSTED_ORIGIN)

    from backend.api.v1.endpoints import recordings as recordings_module
    from backend.utils import recording_storage

    def temp_dir(recording_id: int, create: bool = False) -> Path:
        path = tmp_path / f"upload-{recording_id}"
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    for module in (recordings_module, recording_storage):
        monkeypatch.setattr(module, "recordings_root_dir", lambda create=True: tmp_path)
        monkeypatch.setattr(module, "recording_upload_temp_dir", temp_dir)

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=TRUSTED_ORIGIN
    ) as async_client:
        yield async_client


def _session(client: AsyncClient, username: str) -> None:
    client.cookies.set(
        "access_token",
        security.create_access_token(
            username,
            token_type=security.SESSION_TOKEN_TYPE,
            scopes=[security.WEB_SESSION_SCOPE],
            token_version=0,
        ),
        domain="nojoin.example.com",
        path="/",
    )


def _api_token(username: str) -> dict[str, str]:
    token = security.create_access_token(
        username,
        token_type=security.API_TOKEN_TYPE,
        scopes=[security.API_ACCESS_SCOPE],
        token_version=0,
    )
    return {"Authorization": f"Bearer {token}"}


async def _stored_time(session_maker: sessionmaker, username: str):
    async with session_maker() as session:
        return (
            await session.execute(
                text("SELECT legal_accepted_at FROM users WHERE username = :name"),
                {"name": username},
            )
        ).scalar_one()


async def _start_capture(client: AsyncClient, **kwargs):
    return await client.post("/api/v1/recordings/init", **kwargs)


async def _start_import(client: AsyncClient, kind: str, **kwargs):
    if kind == "chunked":
        return await client.post(
            "/api/v1/recordings/import/chunked/init?filename=call.wav", **kwargs
        )
    route = (
        "/api/v1/recordings/import" if kind == "import" else "/api/v1/recordings/upload"
    )
    return await client.post(
        route, files={"file": ("call.wav", _wav_bytes(), "audio/wav")}, **kwargs
    )


def _assert_refused_for_terms(response) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == LEGAL_ACCEPTANCE_REQUIRED_CODE


# Who is asked.


@pytest.mark.parametrize(
    ("is_superuser", "accepted", "required"),
    [
        (False, None, True),
        (False, ACCEPTED_AT, False),
        # The setup account: its owner accepted on the wizard's Legal step.
        (True, None, False),
        (True, ACCEPTED_AT, False),
    ],
)
def test_everyone_but_the_setup_account_accepts_once(
    is_superuser: bool, accepted: datetime | None, required: bool
) -> None:
    assert (
        needs_legal_acceptance(is_superuser=is_superuser, legal_accepted_at=accepted)
        is required
    )


def test_the_api_gives_the_time_as_it_gives_created_at() -> None:
    """Naive UTC, with no offset, which the web app reads as UTC."""
    user = User(
        id=7,
        username="erin",
        hashed_password="x",
        legal_accepted_at=ACCEPTED_AT,
        created_at=ACCEPTED_AT,
    )

    read = UserRead.model_validate(user, from_attributes=True).model_dump(mode="json")

    assert read["legal_accepted_at"] == read["created_at"] == "2026-11-02T09:30:00"
    assert read["legal_acceptance_required"] is False


def test_sign_in_tells_an_api_client_to_accept_first() -> None:
    new_user = User(username="erin", hashed_password="x")
    setup_account = User(username="owner", hashed_password="x", is_superuser=True)

    assert _build_login_metadata(new_user)["legal_acceptance_required"] is True
    assert _build_login_metadata(setup_account)["legal_acceptance_required"] is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("username", "required"),
    # The owner of an install set up before the time was recorded is not asked;
    # an Admin is asked like anyone else.
    [("owner", False), ("alice", True), ("bob", False), ("carol", True)],
)
async def test_users_me_says_whether_to_ask(
    client: AsyncClient, username: str, required: bool
) -> None:
    _session(client, username)

    body = (await client.get("/api/v1/users/me")).json()

    assert body["legal_acceptance_required"] is required
    assert body["legal_accepted_at"] == (
        "2026-11-02T09:30:00" if username == "bob" else None
    )


@pytest.mark.anyio
async def test_the_users_table_shows_each_acceptance(client: AsyncClient) -> None:
    _session(client, "owner")

    rows = {
        row["username"]: (row["legal_accepted_at"], row["legal_acceptance_required"])
        for row in (await client.get("/api/v1/users")).json()["items"]
    }

    assert rows["owner"] == (None, False)
    assert rows["alice"] == (None, True)
    assert rows["bob"] == ("2026-11-02T09:30:00", False)
    assert rows["carol"] == (None, True)


# New captures and imports.


@pytest.mark.anyio
@pytest.mark.parametrize("credential", ["session", "api_token"])
async def test_a_new_capture_waits_for_the_terms(
    client: AsyncClient, session_maker: sessionmaker, credential: str
) -> None:
    if credential == "session":
        _session(client, "alice")
        kwargs: dict = {"headers": ORIGIN}
    else:
        kwargs = {"headers": _api_token("alice")}

    _assert_refused_for_terms(await _start_capture(client, **kwargs))
    async with session_maker() as session:
        assert (
            await session.execute(text("SELECT count(*) FROM recordings"))
        ).scalar_one() == 0


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["chunked", "import", "upload"])
@pytest.mark.parametrize("credential", ["session", "api_token"])
async def test_a_new_import_waits_for_the_terms(
    client: AsyncClient, session_maker: sessionmaker, kind: str, credential: str
) -> None:
    if credential == "session":
        _session(client, "carol")
        kwargs: dict = {"headers": ORIGIN}
    else:
        kwargs = {"headers": _api_token("carol")}

    _assert_refused_for_terms(await _start_import(client, kind, **kwargs))
    async with session_maker() as session:
        assert (
            await session.execute(text("SELECT count(*) FROM recordings"))
        ).scalar_one() == 0


@pytest.mark.anyio
@pytest.mark.parametrize("username", ["owner", "bob"])
async def test_the_setup_account_and_an_accepted_user_record_as_before(
    client: AsyncClient, username: str
) -> None:
    _session(client, username)

    assert (await _start_capture(client, headers=ORIGIN)).status_code == 200
    response = await _start_import(client, "chunked", headers=ORIGIN)
    assert response.status_code == 200, response.text


@pytest.mark.anyio
async def test_a_password_change_comes_before_the_terms(client: AsyncClient) -> None:
    _session(client, "dave")

    for response in (
        await _start_capture(client, headers=ORIGIN),
        await client.post("/api/v1/users/me/legal-acceptance", headers=ORIGIN),
    ):
        assert response.status_code == 403
        assert response.json()["detail"] == "Password change required"


# What stays open.


@pytest.mark.anyio
async def test_a_capture_running_at_the_upgrade_is_never_cut_off(
    client: AsyncClient, session_maker: sessionmaker
) -> None:
    """Started before the upgrade, then the user is found not to have accepted."""
    _session(client, "bob")
    started = (await _start_capture(client, headers=ORIGIN)).json()
    async with session_maker() as session:
        await session.execute(
            text("UPDATE users SET legal_accepted_at = NULL WHERE username = 'bob'")
        )
        await session.commit()
    recording, epoch = started["id"], started["capture_epoch"]

    paused = await client.post(
        f"/api/v1/recordings/{recording}/pause?epoch={epoch}", headers=ORIGIN
    )
    resumed = await client.post(
        f"/api/v1/recordings/{recording}/resume?epoch={epoch}", headers=ORIGIN
    )
    discarded = await client.post(
        f"/api/v1/recordings/{recording}/discard?epoch={epoch}", headers=ORIGIN
    )

    assert [paused.status_code, resumed.status_code, discarded.status_code] == [
        200,
        200,
        200,
    ]
    _assert_refused_for_terms(await _start_capture(client, headers=ORIGIN))


@pytest.mark.anyio
async def test_an_import_under_way_at_the_upgrade_sends_its_parts(
    client: AsyncClient, session_maker: sessionmaker
) -> None:
    _session(client, "bob")
    recording = (await _start_import(client, "chunked", headers=ORIGIN)).json()["id"]
    async with session_maker() as session:
        await session.execute(
            text("UPDATE users SET legal_accepted_at = NULL WHERE username = 'bob'")
        )
        await session.commit()

    response = await client.post(
        f"/api/v1/recordings/import/chunked/segment?recording_id={recording}&sequence=1",
        files={"file": ("part", b"\x00" * 64, "application/octet-stream")},
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text


@pytest.mark.anyio
async def test_reading_stays_open_before_acceptance(client: AsyncClient) -> None:
    _session(client, "alice")

    assert (await client.get("/api/v1/users/me")).status_code == 200
    assert (await client.get("/api/v1/recordings")).status_code == 200


# Accepting.


@pytest.mark.anyio
async def test_accepting_stamps_the_server_time_once(
    client: AsyncClient, session_maker: sessionmaker
) -> None:
    _session(client, "alice")
    before = utc_now()

    first = await client.post("/api/v1/users/me/legal-acceptance", headers=ORIGIN)
    again = await client.post("/api/v1/users/me/legal-acceptance", headers=ORIGIN)

    assert first.status_code == again.status_code == 200
    assert first.json()["legal_acceptance_required"] is False
    stamped = datetime.fromisoformat(first.json()["legal_accepted_at"])
    # Naive, like every other time the API gives, and from the server's UTC clock.
    assert stamped.tzinfo is None
    assert before - timedelta(seconds=1) <= stamped <= utc_now()
    # Idempotent: the second answers as the first and overwrites nothing.
    assert again.json()["legal_accepted_at"] == first.json()["legal_accepted_at"]
    assert await _stored_time(session_maker, "alice") is not None
    assert (await _start_capture(client, headers=ORIGIN)).status_code == 200


@pytest.mark.anyio
async def test_accepting_never_moves_an_earlier_time(
    client: AsyncClient, session_maker: sessionmaker
) -> None:
    _session(client, "bob")

    response = await client.post("/api/v1/users/me/legal-acceptance", headers=ORIGIN)

    assert response.json()["legal_accepted_at"] == "2026-11-02T09:30:00"


@pytest.mark.anyio
async def test_only_a_browser_session_accepts(
    client: AsyncClient, session_maker: sessionmaker
) -> None:
    response = await client.post(
        "/api/v1/users/me/legal-acceptance", headers=_api_token("alice")
    )

    assert response.status_code == 403
    assert await _stored_time(session_maker, "alice") is None


@pytest.mark.anyio
async def test_no_user_update_sets_or_clears_the_time(
    client: AsyncClient, session_maker: sessionmaker
) -> None:
    _session(client, "owner")
    for response in (
        await client.patch(
            "/api/v1/users/2",
            json={"legal_accepted_at": "2026-01-01T00:00:00Z"},
            headers=ORIGIN,
        ),
        await client.patch(
            "/api/v1/users/3", json={"legal_accepted_at": None}, headers=ORIGIN
        ),
        await client.put(
            "/api/v1/users/me",
            json={"legal_accepted_at": "2026-01-01T00:00:00Z"},
            headers=ORIGIN,
        ),
    ):
        assert response.status_code == 200, response.text
    _session(client, "alice")
    await client.put(
        "/api/v1/users/me",
        json={"legal_accepted_at": "2026-01-01T00:00:00Z"},
        headers=ORIGIN,
    )

    assert await _stored_time(session_maker, "alice") is None
    assert await _stored_time(session_maker, "bob") is not None
    assert await _stored_time(session_maker, "owner") is None


# Where the refusal sits.

RECORDING_ROUTES = REPO_ROOT / "backend" / "api" / "v1" / "endpoints" / "recordings"
REFUSING_ROUTES = {
    "init_upload",
    "import_audio",
    "init_chunked_import",
    "upload_recording",
}


def _functions(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)]


def _calls(function: ast.AsyncFunctionDef) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_every_route_that_creates_a_recording_refuses_first() -> None:
    """A new route that creates a recording from audio must refuse too."""
    creating = {
        function.name
        for path in sorted(RECORDING_ROUTES.glob("*.py"))
        for function in _functions(path)
        if "Recording" in _calls(function)
    }
    refusing = {
        function.name
        for path in sorted(RECORDING_ROUTES.glob("*.py"))
        for function in _functions(path)
        if "enforce_legal_acceptance" in _calls(function)
    }

    assert creating == REFUSING_ROUTES
    # Segments, pause, resume, finalize and discard never refuse.
    assert refusing == REFUSING_ROUTES


def test_mcp_cannot_create_a_recording() -> None:
    """No MCP tool records or imports, so the connector has nothing to refuse."""
    for path in sorted((REPO_ROOT / "backend" / "mcp_server").glob("*.py")):
        for function in _functions(path):
            assert not (_calls(function) & ({"Recording"} | REFUSING_ROUTES)), (
                f"{path.name}:{function.name} creates a recording; refuse it with "
                "enforce_legal_acceptance as the recording routes do."
            )


def test_a_direct_call_cannot_skip_the_refusal() -> None:
    """The check sits in the route body, not a dependency an MCP tool would skip."""
    with pytest.raises(Exception) as refused:
        enforce_legal_acceptance(
            SimpleNamespace(is_superuser=False, legal_accepted_at=None)  # type: ignore[arg-type]
        )

    assert refused.value.detail["code"] == LEGAL_ACCEPTANCE_REQUIRED_CODE  # type: ignore[attr-defined]


# The owner of a new install.


class _SetupSession:
    """Just enough session for first-run setup on an empty install."""

    def __init__(self) -> None:
        self.added: list = []

    async def execute(self, statement):
        initialized = bool(self.added)
        return SimpleNamespace(
            scalar_one_or_none=lambda: object() if initialized else None
        )

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="fake"))

    def add(self, value) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        return None

    async def refresh(self, value) -> None:
        value.id = 1


@pytest.mark.anyio
async def test_setup_records_when_the_owner_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.v1.endpoints import setup, system
    from backend.main import create_app
    from backend.utils import telemetry

    async def _none(*args, **kwargs) -> None:
        return None

    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, "an-operator-secret")
    monkeypatch.setattr(system, "enforce_setup_rate_limit", _none)
    monkeypatch.setattr(system, "seed_demo_data", _none)
    monkeypatch.setattr(system, "enqueue_model_preparation", _none)
    monkeypatch.setattr(system, "_save_first_run_whisper_size", lambda size: size)
    monkeypatch.setattr(telemetry, "record_first_run_choice", lambda choice: None)
    session = _SetupSession()

    async def _db():
        yield session

    app = create_app(app_lifespan=None)
    app.dependency_overrides[get_db] = _db
    before = utc_now()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as setup_client:
        response = await setup_client.post(
            "/api/v1/system/setup",
            headers={"Authorization": "Bootstrap an-operator-secret"},
            json={
                "username": "alice.example",
                "password": "password123",
                "include_demo_recording": False,
            },
        )

    assert response.status_code == 200, response.text
    (owner,) = session.added
    assert owner.is_superuser
    assert owner.legal_accepted_at.tzinfo is None
    assert before <= owner.legal_accepted_at <= utc_now()
