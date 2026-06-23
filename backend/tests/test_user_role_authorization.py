"""Authorization tests locking the owner-role rules in the users endpoints.

These tests pin the policy that the comments in
``backend/api/v1/endpoints/users.py`` describe (SRC-006): only an owner may
delete an owner, change an owner's role, or promote a user to the owner role.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.v1.endpoints import users
from backend.models.user import UserRole, UserUpdate


class _Result:
    def __init__(self, scalars_all: list[object] | None = None):
        self._scalars_all = scalars_all or []

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._scalars_all))


class _FakeSession:
    """Minimal async session: serves users by id and records mutations."""

    def __init__(
        self,
        users_by_id: dict[int, object],
        execute_results: list[object] | None = None,
    ):
        self._users = users_by_id
        self._execute_results = list(execute_results or [])
        self.deleted: list[object] = []
        self.added: list[object] = []
        self.committed = False
        self.rolled_back = False

    async def get(self, _model, pk):
        return self._users.get(pk)

    async def execute(self, _statement):
        if self._execute_results:
            return self._execute_results.pop(0)
        return _Result(scalars_all=[])

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    def add(self, obj):
        self.added.append(obj)

    async def refresh(self, _obj):
        return None


def _user(user_id: int, role: str, *, is_superuser: bool = False):
    return SimpleNamespace(
        id=user_id,
        role=role,
        is_superuser=is_superuser,
        username=f"user{user_id}",
    )


# --- delete_user ---------------------------------------------------------


@pytest.mark.anyio
async def test_admin_cannot_delete_owner() -> None:
    owner = _user(2, UserRole.OWNER)
    db = _FakeSession({2: owner})

    with pytest.raises(HTTPException) as exc_info:
        await users.delete_user(
            db=db,
            user_id=2,
            current_user=_user(1, UserRole.ADMIN),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Cannot delete the owner"
    assert db.deleted == []


@pytest.mark.anyio
async def test_owner_can_delete_another_owner() -> None:
    target_owner = _user(2, UserRole.OWNER)
    db = _FakeSession({2: target_owner})

    result = await users.delete_user(
        db=db,
        user_id=2,
        current_user=_user(1, UserRole.OWNER),
    )

    assert result is target_owner
    assert db.deleted == [target_owner]
    assert db.committed is True


# --- update_user_role ----------------------------------------------------


@pytest.mark.anyio
async def test_admin_cannot_change_owner_role() -> None:
    owner = _user(2, UserRole.OWNER)
    db = _FakeSession({2: owner})

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user_role(
            db=db,
            user_id=2,
            role=UserRole.USER,
            current_user=_user(1, UserRole.ADMIN),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Cannot modify the owner"


@pytest.mark.anyio
async def test_admin_cannot_promote_user_to_owner() -> None:
    member = _user(2, UserRole.USER)
    db = _FakeSession({2: member})

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user_role(
            db=db,
            user_id=2,
            role=UserRole.OWNER,
            current_user=_user(1, UserRole.ADMIN),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Cannot promote to owner"


@pytest.mark.anyio
async def test_owner_can_promote_user_to_owner() -> None:
    member = _user(2, UserRole.USER)
    db = _FakeSession({2: member})

    result = await users.update_user_role(
        db=db,
        user_id=2,
        role=UserRole.OWNER,
        current_user=_user(1, UserRole.OWNER),
    )

    assert result.role == UserRole.OWNER
    assert db.committed is True


# --- update_user (superuser PATCH) ---------------------------------------


@pytest.mark.anyio
async def test_superuser_admin_cannot_change_owner_role() -> None:
    owner = _user(2, UserRole.OWNER)
    db = _FakeSession({2: owner})

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user(
            db=db,
            user_id=2,
            user_in=UserUpdate(role=UserRole.USER),
            current_user=_user(1, UserRole.ADMIN, is_superuser=True),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Cannot modify the owner"


@pytest.mark.anyio
async def test_superuser_admin_cannot_promote_user_to_owner() -> None:
    member = _user(2, UserRole.USER)
    db = _FakeSession({2: member})

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user(
            db=db,
            user_id=2,
            user_in=UserUpdate(role=UserRole.OWNER),
            current_user=_user(1, UserRole.ADMIN, is_superuser=True),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Cannot promote to owner"
