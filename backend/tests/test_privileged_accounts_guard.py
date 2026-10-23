"""No change through the users routes may leave no active Owner or superuser.

Granting the Owner role takes an Owner (#634), and granting superuser rights
takes a superuser (#640), so the routes that can take either, or an active
account, from someone refuse to take the last. Each takes the locks before it
reads its target; ``test_privileged_accounts_guard_postgres.py`` runs the real
locks against two requests at once. Here the lock is replaced by the accounts
it would have found.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.api.v1.endpoints import users
from backend.models.user import UserRole, UserUpdate
from backend.tests.test_user_role_authorization import _FakeSession, _user


class _Session(_FakeSession):
    """Records when the target is read, to order it against the lock."""

    def __init__(self, users_by_id, events: list[str]):
        super().__init__(users_by_id)
        self._events = events

    async def get(self, model, pk, **options):
        self._events.append("read target")
        return await super().get(model, pk, **options)


@pytest.fixture
def locked(monkeypatch):
    """Serve the given active Owners and superusers from the lock, and log it."""
    state: dict = {"owners": set(), "superusers": set(), "events": []}

    async def lock(_db):
        state["events"].append("lock")
        return users.PrivilegedAccounts(
            owner_ids=frozenset(state["owners"]),
            superuser_ids=frozenset(state["superusers"]),
        )

    monkeypatch.setattr(users, "lock_privileged_accounts", lock)
    return state


def _account(user_id: int, role: str, *, superuser: bool = False):
    account = _user(user_id, role, is_superuser=superuser)
    account.is_active = True
    account.token_version = 0
    return account


def _refused(exc_info, detail: str, status: int = 409) -> None:
    assert exc_info.value.status_code == status
    assert exc_info.value.detail == detail


# --- the last active Owner (#634) ------------------------------------------


@pytest.mark.anyio
async def test_the_last_owner_cannot_give_up_the_role(locked) -> None:
    me = _account(1, UserRole.OWNER)
    locked["owners"] = {1}
    db = _Session({1: me}, locked["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user_role(
            db=db, user_id=1, role=UserRole.ADMIN, current_user=me
        )

    _refused(exc_info, users.LAST_OWNER_DETAIL)
    assert me.role == UserRole.OWNER
    assert db.committed is False
    assert locked["events"] == ["lock", "read target"]


@pytest.mark.anyio
async def test_an_owner_can_step_down_while_another_remains(locked) -> None:
    me = _account(1, UserRole.OWNER)
    locked["owners"] = {1, 2}
    db = _Session({1: me, 2: _account(2, UserRole.OWNER)}, locked["events"])

    result = await users.update_user_role(
        db=db, user_id=1, role=UserRole.USER, current_user=me
    )

    assert result.role == UserRole.USER
    assert db.committed is True


@pytest.mark.anyio
async def test_an_inactive_owner_does_not_count(locked) -> None:
    # The lock finds active Owners only: one who cannot sign in cannot grant
    # the role either.
    me = _account(1, UserRole.OWNER)
    locked["owners"] = {1}
    db = _Session({1: me, 2: _account(2, UserRole.OWNER)}, locked["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user_role(
            db=db, user_id=1, role=UserRole.ADMIN, current_user=me
        )

    _refused(exc_info, users.LAST_OWNER_DETAIL)


@pytest.mark.anyio
async def test_promoting_to_owner_takes_no_lock(locked) -> None:
    member = _account(2, UserRole.USER)
    db = _Session({2: member}, locked["events"])

    await users.update_user_role(
        db=db, user_id=2, role=UserRole.OWNER, current_user=_user(1, UserRole.OWNER)
    )

    assert locked["events"] == ["read target"]
    assert member.role == UserRole.OWNER


@pytest.mark.anyio
async def test_the_setup_account_cannot_demote_the_last_owner(locked) -> None:
    me = _account(1, UserRole.OWNER, superuser=True)
    locked["owners"] = {1}
    locked["superusers"] = {1}
    db = _Session({1: me}, locked["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user(
            db=db, user_id=1, user_in=UserUpdate(role=UserRole.ADMIN), current_user=me
        )

    _refused(exc_info, users.LAST_OWNER_DETAIL)
    assert db.committed is False
    assert locked["events"] == ["lock", "read target"]


@pytest.mark.anyio
async def test_the_setup_account_cannot_deactivate_the_last_owner(locked) -> None:
    owner = _account(2, UserRole.OWNER)
    locked["owners"] = {2}
    locked["superusers"] = {1}
    db = _Session({2: owner}, locked["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user(
            db=db,
            user_id=2,
            user_in=UserUpdate(is_active=False),
            current_user=_account(1, UserRole.ADMIN, superuser=True),
        )

    _refused(exc_info, users.LAST_OWNER_DETAIL)
    assert owner.is_active is True


@pytest.mark.anyio
async def test_the_setup_account_can_deactivate_others(locked) -> None:
    member = _account(2, UserRole.USER)
    locked["owners"] = {1}
    locked["superusers"] = {1}
    db = _Session({2: member}, locked["events"])

    result = await users.update_user(
        db=db,
        user_id=2,
        user_in=UserUpdate(is_active=False),
        current_user=_account(1, UserRole.OWNER, superuser=True),
    )

    assert result.is_active is False
    assert db.committed is True


@pytest.mark.anyio
async def test_an_edit_that_keeps_access_takes_no_lock(locked) -> None:
    me = _account(1, UserRole.OWNER, superuser=True)
    db = _Session({1: me}, locked["events"])

    await users.update_user(
        db=db,
        user_id=1,
        user_in=UserUpdate(ai_analysis_daily_limit=5, is_active=True),
        current_user=me,
    )

    assert locked["events"] == ["read target"]
    assert db.committed is True


@pytest.mark.anyio
async def test_a_delete_counts_the_owners_the_lock_found(locked) -> None:
    # The caller was an Owner when they signed in, but another request has
    # since taken the role from them: the target is the last Owner left.
    target = _account(2, UserRole.OWNER)
    locked["owners"] = {2}
    db = _Session({2: target}, locked["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.delete_user(db=db, user_id=2, current_user=_user(1, UserRole.OWNER))

    _refused(exc_info, users.LAST_OWNER_DETAIL)
    assert db.deleted == []
    assert locked["events"] == ["lock", "read target"]


# --- the last active superuser (#640) --------------------------------------


@pytest.mark.anyio
async def test_the_setup_account_cannot_deactivate_itself(locked) -> None:
    # Even with another superuser left: it would sign itself out for good.
    me = _account(1, UserRole.OWNER, superuser=True)
    locked["owners"] = {1, 2}
    locked["superusers"] = {1, 2}
    db = _Session({1: me}, locked["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user(
            db=db, user_id=1, user_in=UserUpdate(is_active=False), current_user=me
        )

    _refused(exc_info, "Cannot deactivate yourself", status=400)
    assert me.is_active is True
    assert db.committed is False


@pytest.mark.anyio
async def test_the_last_superuser_keeps_its_rights(locked) -> None:
    me = _account(1, UserRole.OWNER, superuser=True)
    locked["owners"] = {1, 2}
    locked["superusers"] = {1}
    db = _Session({1: me}, locked["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user(
            db=db, user_id=1, user_in=UserUpdate(is_superuser=False), current_user=me
        )

    _refused(exc_info, users.LAST_SUPERUSER_DETAIL)
    assert me.is_superuser is True
    assert db.committed is False


@pytest.mark.anyio
async def test_a_superuser_can_give_up_its_rights_to_another(locked) -> None:
    me = _account(1, UserRole.OWNER, superuser=True)
    locked["superusers"] = {1, 2}
    db = _Session({1: me}, locked["events"])

    result = await users.update_user(
        db=db, user_id=1, user_in=UserUpdate(is_superuser=False), current_user=me
    )

    assert result.is_superuser is False
    assert db.committed is True


@pytest.mark.anyio
async def test_no_one_can_deactivate_the_last_superuser(locked) -> None:
    # As when the caller's own rights went to a change made meanwhile.
    target = _account(2, UserRole.ADMIN, superuser=True)
    locked["superusers"] = {2}
    db = _Session({2: target}, locked["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.update_user(
            db=db,
            user_id=2,
            user_in=UserUpdate(is_active=False),
            current_user=_account(1, UserRole.OWNER, superuser=True),
        )

    _refused(exc_info, users.LAST_SUPERUSER_DETAIL)


@pytest.mark.anyio
async def test_the_last_superuser_cannot_be_deleted(locked) -> None:
    target = _account(2, UserRole.OWNER, superuser=True)
    locked["owners"] = {1, 2}
    locked["superusers"] = {2}
    db = _Session({2: target}, locked["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.delete_user(
            db=db, user_id=2, current_user=_account(1, UserRole.OWNER)
        )

    _refused(exc_info, users.LAST_SUPERUSER_DETAIL)
    assert db.deleted == []


@pytest.mark.anyio
async def test_the_only_superuser_cannot_be_deleted_while_inactive(locked) -> None:
    # As before #640: the one account that could be reactivated with
    # superuser rights stays, even though it cannot sign in.
    target = _account(2, UserRole.ADMIN, superuser=True)
    target.is_active = False
    db = _Session({2: target}, locked["events"])

    with pytest.raises(HTTPException) as exc_info:
        await users.delete_user(
            db=db, user_id=2, current_user=_account(1, UserRole.OWNER)
        )

    _refused(exc_info, users.LAST_SUPERUSER_DETAIL)
