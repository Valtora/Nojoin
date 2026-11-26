"""The access rules in backend/services/recording_access.py."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import select

from backend.models.recording import Recording
from backend.models.recording_share import RecordingShare
from backend.services.recording_access import (
    Access,
    access_level,
    access_levels_for,
    accessible_recordings_filter,
    get_recording_for,
    shared_with_filter,
)
from backend.tests.sso_sharing_support import build_harness


@pytest.fixture
async def harness():
    async for built in build_harness():
        yield built


async def _share(harness, recording, *, user=None, group=None, level="view"):
    async with harness.session_maker() as session:
        session.add(
            RecordingShare(
                recording_id=recording.id,
                user_id=user.id if user else None,
                group_name=group,
                level=level,
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_owner_has_owner_access(harness) -> None:
    owner = await harness.add_user("owner")
    recording = await harness.add_recording(owner)
    async with harness.session_maker() as session:
        assert await access_level(session, recording, owner) is Access.OWNER


@pytest.mark.anyio
async def test_strangers_get_not_found(harness) -> None:
    owner = await harness.add_user("owner")
    stranger = await harness.add_user("stranger")
    recording = await harness.add_recording(owner)
    async with harness.session_maker() as session:
        with pytest.raises(HTTPException) as refused:
            await get_recording_for(session, recording.public_id, stranger, Access.VIEW)
    assert refused.value.status_code == 404
    assert refused.value.detail == "Recording not found"


@pytest.mark.anyio
async def test_insufficient_level_is_forbidden(harness) -> None:
    owner = await harness.add_user("owner")
    viewer = await harness.add_user("viewer")
    recording = await harness.add_recording(owner)
    await _share(harness, recording, user=viewer, level="view")
    async with harness.session_maker() as session:
        found, level = await get_recording_for(
            session, recording.public_id, viewer, Access.VIEW
        )
        assert found.id == recording.id and level is Access.VIEW
        with pytest.raises(HTTPException) as refused:
            await get_recording_for(session, recording.public_id, viewer, Access.EDIT)
    assert refused.value.status_code == 403


@pytest.mark.anyio
async def test_group_share_reaches_members_only(harness) -> None:
    owner = await harness.add_user("owner")
    member = await harness.add_user("member", groups=["research"])
    outsider = await harness.add_user("outsider", groups=["sales"])
    recording = await harness.add_recording(owner)
    await _share(harness, recording, group="research", level="edit")
    async with harness.session_maker() as session:
        assert await access_level(session, recording, member) is Access.EDIT
        assert await access_level(session, recording, outsider) is Access.NONE


@pytest.mark.anyio
async def test_highest_applicable_share_wins(harness) -> None:
    owner = await harness.add_user("owner")
    member = await harness.add_user("member", groups=["research"])
    recording = await harness.add_recording(owner)
    await _share(harness, recording, user=member, level="view")
    await _share(harness, recording, group="research", level="edit")
    async with harness.session_maker() as session:
        assert await access_level(session, recording, member) is Access.EDIT
        levels = await access_levels_for(session, [recording], member)
    assert levels == {recording.id: Access.EDIT}


@pytest.mark.anyio
async def test_list_filters(harness) -> None:
    alice = await harness.add_user("alice", groups=["research"])
    bob = await harness.add_user("bob")
    own = await harness.add_recording(alice, name="mine")
    direct = await harness.add_recording(bob, name="direct")
    via_group = await harness.add_recording(bob, name="group")
    hidden = await harness.add_recording(bob, name="hidden")
    await _share(harness, direct, user=alice)
    await _share(harness, via_group, group="research")
    async with harness.session_maker() as session:
        visible = (
            await session.execute(
                select(Recording.name).where(accessible_recordings_filter(alice))
            )
        ).scalars()
        shared = (
            await session.execute(
                select(Recording.name).where(shared_with_filter(alice))
            )
        ).scalars()
        visible, shared = set(visible), set(shared)
    assert visible == {"mine", "direct", "group"}
    assert shared == {"direct", "group"}
    assert hidden.name not in visible
    assert own.name not in shared


@pytest.mark.anyio
async def test_shares_disappear_with_the_recording(harness) -> None:
    owner = await harness.add_user("owner")
    viewer = await harness.add_user("viewer")
    recording = await harness.add_recording(owner)
    await _share(harness, recording, user=viewer)
    async with harness.session_maker() as session:
        await session.delete(await session.get(Recording, recording.id))
        await session.commit()
        remaining = (await session.execute(select(RecordingShare))).scalars().all()
    assert remaining == []
