from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.api.v1.endpoints import invitations, users
from backend.models.user import UserCreate, UserRole
from backend.models.invitation import Invitation
from backend.models.tag import RecordingTag  # noqa: F401
from backend.models.speaker import RecordingSpeaker  # noqa: F401
from backend.models.transcript import Transcript  # noqa: F401
from backend.models.chat import ChatMessage  # noqa: F401
from backend.models.document import Document  # noqa: F401
from backend.models.context_chunk import ContextChunk  # noqa: F401
from backend.utils.time import utc_now


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeWritableSession:
    def __init__(self, execute_results: list[object] | None = None):
        self._execute_results = list(execute_results or [])
        self.added: list[object] = []

    async def execute(self, statement):
        if not self._execute_results:
            raise AssertionError("Unexpected execute() call")
        return _ScalarResult(self._execute_results.pop(0))

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        return None

    async def refresh(self, value):
        if getattr(value, "id", None) is None:
            value.id = 1


async def _allow_request(*args, **kwargs) -> None:
    return None


def _make_request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )


@pytest.mark.anyio
async def test_admin_can_create_user_invitation() -> None:
    db = _FakeWritableSession()

    invitation = await invitations.create_invitation(
        db=db,
        invitation_in=invitations.InvitationCreate(role=UserRole.USER),
        current_user=SimpleNamespace(
            id=7,
            role=UserRole.ADMIN,
            is_superuser=False,
        ),
    )

    assert invitation.role == UserRole.USER
    assert db.added[0].role == UserRole.USER


@pytest.mark.anyio
async def test_owner_can_create_admin_invitation() -> None:
    db = _FakeWritableSession()

    invitation = await invitations.create_invitation(
        db=db,
        invitation_in=invitations.InvitationCreate(role=UserRole.ADMIN),
        current_user=SimpleNamespace(
            id=9,
            role=UserRole.OWNER,
            is_superuser=False,
        ),
    )

    assert invitation.role == UserRole.ADMIN
    assert db.added[0].role == UserRole.ADMIN


@pytest.mark.anyio
async def test_create_invitation_rejects_owner_role() -> None:
    db = _FakeWritableSession()

    with pytest.raises(HTTPException) as exc_info:
        await invitations.create_invitation(
            db=db,
            invitation_in=invitations.InvitationCreate(role=UserRole.OWNER),
            current_user=SimpleNamespace(
                id=11,
                role=UserRole.ADMIN,
                is_superuser=False,
            ),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Owner invitations are not allowed"


@pytest.mark.anyio
async def test_create_invitation_rejects_unknown_role() -> None:
    db = _FakeWritableSession()

    with pytest.raises(HTTPException) as exc_info:
        await invitations.create_invitation(
            db=db,
            invitation_in=invitations.InvitationCreate(role="superadmin"),
            current_user=SimpleNamespace(
                id=12,
                role=UserRole.OWNER,
                is_superuser=False,
            ),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid invitation role"


@pytest.mark.anyio
@pytest.mark.parametrize("persisted_role", [UserRole.OWNER, "legacy-admin"])
async def test_invitation_validation_rejects_invalid_persisted_roles(
    monkeypatch: pytest.MonkeyPatch,
    persisted_role: str,
) -> None:
    monkeypatch.setattr(invitations, "enforce_rate_limit", _allow_request)
    db = _FakeWritableSession(
        [
            SimpleNamespace(
                code="invite123",
                role=persisted_role,
                is_revoked=False,
                expires_at=None,
                max_uses=None,
                used_count=0,
            )
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        await invitations.validate_invitation(
            request=_make_request("GET", "/api/v1/invitations/validate/invite123"),
            code="invite123",
            db=db,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invitation is invalid"


@pytest.mark.anyio
@pytest.mark.parametrize("persisted_role", [UserRole.OWNER, "legacy-admin"])
async def test_registration_rejects_invalid_persisted_invitation_roles(
    monkeypatch: pytest.MonkeyPatch,
    persisted_role: str,
) -> None:
    monkeypatch.setattr(users, "enforce_rate_limit", _allow_request)
    db = _FakeWritableSession(
        [
            SimpleNamespace(
                id=21,
                code="invite123",
                role=persisted_role,
                is_revoked=False,
                expires_at=None,
                max_uses=None,
                used_count=0,
            ),
            None,
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        await users.register_user(
            request=_make_request("POST", "/api/v1/users/register"),
            db=db,
            user_in=UserCreate(
                username="new-user",
                password="validpass",
                invite_code="invite123",
            ),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invitation is invalid"


@pytest.mark.anyio
async def test_register_user_uses_with_for_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(users, "enforce_rate_limit", _allow_request)
    
    executed_queries = []
    
    class InterceptingSession:
        def __init__(self):
            self.invitation = SimpleNamespace(
                id=1,
                code="invite123",
                role=UserRole.USER,
                is_revoked=False,
                expires_at=None,
                max_uses=1,
                used_count=0,
            )
            self.added = []
            
        async def execute(self, statement):
            executed_queries.append(statement)
            if "invitations" in str(statement.compile()):
                return _ScalarResult(self.invitation)
            else:
                return _ScalarResult(None)
                
        def add(self, value):
            self.added.append(value)
            
        async def commit(self):
            pass
            
        async def refresh(self, value):
            value.id = 1

    session = InterceptingSession()
    
    await users.register_user(
        request=_make_request("POST", "/api/v1/users/register"),
        db=session,
        user_in=UserCreate(
            username="new-user",
            password="validpassword123",
            invite_code="invite123",
        ),
    )
    
    # Assert that the invitation query had with_for_update called on it
    invitation_queries = [q for q in executed_queries if "invitations" in str(q.compile())]
    assert len(invitation_queries) == 1
    assert invitation_queries[0]._for_update_arg is not None
