"""No change through the users routes may leave an installation without an Owner (#634).

Granting the Owner role takes an Owner, so the routes that can take the role or
an active account from one refuse to take it from the last. Each takes the
Owner locks before it reads its target; ``test_last_owner_guard_postgres.py``
runs the real locks against two requests at once. Here the lock is replaced by
the set of Owners it would have found.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.api.v1.endpoints import users
from backend.models.user import UserRole, UserUpdate
from backend.tests.test_user_role_authorization import _FakeSession, _user


class _Session(_FakeSession):
    """Records when the target is read, to order it against the Owner lock."""

    def __init__(self, users_by_id, events: list[str]):
        super().__init__(users_by_id)
        self._events = events

    async def get(self, model, pk, **options):
        self._events.append("read target")
        return await super().get(model, pk, **options)


@pytest.fixture
def owners(monkeypatch):
    """Serve the given Owner ids from the lock and log when it is taken."""
    state: dict = {"ids": set(), "events": []}

    async def lock(_db):
        state["events"].append("lock owners")
        return set(state["ids"])

    monkeypatch.setattr(users, "lock_active_owner_ids", lock)
    return state


def _refused(exc_info) -> None:
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == users.LAST_OWNER_DETAIL


@pytest.mark.anyio
async def test_the_last_owner_cannot_give_up_the_role(owners) -> None:
    me = _user(1, UserRole.OWNER)
    owners["ids"] = {1}
    db = _Session({1: me}, owners["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user_role(
            db=db, user_id=1, role=UserRole.ADMIN, current_user=me
        )

    _refused(exc_info)
    assert me.role == UserRole.OWNER
    assert db.committed is False
    assert owners["events"] == ["lock owners", "read target"]


@pytest.mark.anyio
async def test_an_owner_can_step_down_while_another_remains(owners) -> None:
    me = _user(1, UserRole.OWNER)
    owners["ids"] = {1, 2}
    db = _Session({1: me, 2: _user(2, UserRole.OWNER)}, owners["events"])

    result = await users.update_user_role(
        db=db, user_id=1, role=UserRole.USER, current_user=me
    )

    assert result.role == UserRole.USER
    assert db.committed is True


@pytest.mark.anyio
async def test_an_inactive_owner_does_not_count(owners) -> None:
    # The lock finds active Owners only: one who cannot sign in cannot grant
    # the role either.
    me = _user(1, UserRole.OWNER)
    owners["ids"] = {1}
    db = _Session({1: me, 2: _user(2, UserRole.OWNER)}, owners["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user_role(
            db=db, user_id=1, role=UserRole.ADMIN, current_user=me
        )

    _refused(exc_info)


@pytest.mark.anyio
async def test_promoting_to_owner_takes_no_lock(owners) -> None:
    member = _user(2, UserRole.USER)
    db = _Session({2: member}, owners["events"])

    await users.update_user_role(
        db=db, user_id=2, role=UserRole.OWNER, current_user=_user(1, UserRole.OWNER)
    )

    assert owners["events"] == ["read target"]
    assert member.role == UserRole.OWNER


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [UserUpdate(role=UserRole.ADMIN), UserUpdate(is_active=False)],
    ids=["demote", "deactivate"],
)
async def test_the_setup_account_cannot_take_the_last_owner_away(
    owners, change
) -> None:
    me = _user(1, UserRole.OWNER, is_superuser=True)
    me.is_active = True
    owners["ids"] = {1}
    db = _Session({1: me}, owners["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user(db=db, user_id=1, user_in=change, current_user=me)

    _refused(exc_info)
    assert db.committed is False
    assert owners["events"] == ["lock owners", "read target"]


@pytest.mark.anyio
async def test_the_setup_account_can_deactivate_others(owners) -> None:
    member = _user(2, UserRole.USER)
    member.is_active = True
    member.token_version = 0
    owners["ids"] = {1}
    db = _Session({2: member}, owners["events"])

    result = await users.update_user(
        db=db,
        user_id=2,
        user_in=UserUpdate(is_active=False),
        current_user=_user(1, UserRole.OWNER, is_superuser=True),
    )

    assert result.is_active is False
    assert db.committed is True


@pytest.mark.anyio
async def test_an_edit_that_keeps_the_role_takes_no_lock(owners) -> None:
    me = _user(1, UserRole.OWNER, is_superuser=True)
    me.is_active = True
    db = _Session({1: me}, owners["events"])

    await users.update_user(
        db=db,
        user_id=1,
        user_in=UserUpdate(ai_analysis_daily_limit=5, is_active=True),
        current_user=me,
    )

    assert owners["events"] == ["read target"]
    assert db.committed is True


@pytest.mark.anyio
async def test_a_delete_counts_the_owners_the_lock_found(owners) -> None:
    # The caller was an Owner when they signed in, but another request has
    # since taken the role from them: the target is the last Owner left.
    target = _user(2, UserRole.OWNER)
    owners["ids"] = {2}
    db = _Session({2: target}, owners["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.delete_user(db=db, user_id=2, current_user=_user(1, UserRole.OWNER))

    _refused(exc_info)
    assert db.deleted == []
    assert owners["events"] == ["lock owners", "read target"]
