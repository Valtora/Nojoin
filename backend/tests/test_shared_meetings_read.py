"""Reading a meeting someone shared with you: lists, detail, documents, and
what stays personal (chat, tags)."""

from __future__ import annotations

import pytest
from sqlmodel import select

from backend.models.chat import ChatMessage
from backend.models.document import Document, DocumentStatus
from backend.models.recording_share import RecordingShare
from backend.models.tag import RecordingTag, Tag
from backend.tests.sso_sharing_support import build_harness


@pytest.fixture
async def harness():
    async for built in build_harness():
        yield built


@pytest.fixture
async def people(harness):
    owner = await harness.add_user(
        "owner", groups=["research"], display_name="Olive Owner"
    )
    viewer = await harness.add_user("viewer")
    member = await harness.add_user("member", groups=["research"])
    stranger = await harness.add_user("stranger")
    return owner, viewer, member, stranger


async def _list_ids(harness, caller, **params) -> list[str]:
    async with harness.client(caller) as client:
        response = await client.get("/api/v1/recordings/", params=params)
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()]


@pytest.mark.anyio
async def test_owner_shares_with_a_user_and_a_group(harness, people) -> None:
    owner, viewer, member, stranger = people
    recording = await harness.add_recording(owner)
    async with harness.client(owner) as client:
        added = await client.put(
            f"/api/v1/recordings/{recording.public_id}/shares",
            json={"user_id": viewer.id, "level": "view"},
        )
        assert added.status_code == 200, added.text
        added = await client.put(
            f"/api/v1/recordings/{recording.public_id}/shares",
            json={"group": "research", "level": "edit"},
        )
        shares = added.json()
    assert {
        (s["user"]["username"] if s["user"] else None, s["group"], s["level"])
        for s in shares
    } == {
        ("viewer", None, "view"),
        (None, "research", "edit"),
    }

    assert recording.public_id in await _list_ids(harness, viewer)
    assert recording.public_id in await _list_ids(harness, member)
    assert recording.public_id not in await _list_ids(harness, stranger)


@pytest.mark.anyio
async def test_changing_a_level_updates_the_existing_share(harness, people) -> None:
    owner, viewer, *_ = people
    recording = await harness.add_recording(owner)
    async with harness.client(owner) as client:
        url = f"/api/v1/recordings/{recording.public_id}/shares"
        await client.put(url, json={"user_id": viewer.id, "level": "view"})
        shares = (
            await client.put(url, json={"user_id": viewer.id, "level": "edit"})
        ).json()
    assert len(shares) == 1 and shares[0]["level"] == "edit"


@pytest.mark.anyio
async def test_share_requests_are_validated(harness, people) -> None:
    owner, viewer, *_ = people
    recording = await harness.add_recording(owner)
    url = f"/api/v1/recordings/{recording.public_id}/shares"
    async with harness.client(owner) as client:
        both = await client.put(
            url, json={"user_id": viewer.id, "group": "research", "level": "view"}
        )
        neither = await client.put(url, json={"level": "view"})
        self_share = await client.put(url, json={"user_id": owner.id, "level": "edit"})
        unknown = await client.put(url, json={"user_id": 99999, "level": "view"})
        foreign_group = await client.put(url, json={"group": "sales", "level": "view"})
    assert both.status_code == 422
    assert neither.status_code == 422
    assert self_share.status_code == 400
    assert unknown.status_code == 404
    assert foreign_group.status_code == 400
    assert foreign_group.json()["detail"] == "not_a_member_of_group"


@pytest.mark.anyio
async def test_detail_says_who_owns_a_shared_meeting(harness, people) -> None:
    owner, viewer, *_ = people
    recording = await harness.add_recording(owner)
    async with harness.client(owner) as client:
        await client.put(
            f"/api/v1/recordings/{recording.public_id}/shares",
            json={"user_id": viewer.id, "level": "view"},
        )
        own = (await client.get(f"/api/v1/recordings/{recording.public_id}")).json()
    async with harness.client(viewer) as client:
        shared = (await client.get(f"/api/v1/recordings/{recording.public_id}")).json()
        listed = (await client.get("/api/v1/recordings/")).json()
    assert own["access"] == "owner" and own["owner"] is None
    assert shared["access"] == "view"
    assert shared["owner"] == {"username": "owner", "display_name": "Olive Owner"}
    assert listed[0]["access"] == "view"


@pytest.mark.anyio
async def test_scope_filters_the_list(harness, people) -> None:
    owner, viewer, *_ = people
    theirs = await harness.add_recording(owner, name="theirs")
    mine = await harness.add_recording(viewer, name="mine")
    async with harness.session_maker() as session:
        session.add(
            RecordingShare(recording_id=theirs.id, user_id=viewer.id, level="view")
        )
        await session.commit()
    assert set(await _list_ids(harness, viewer)) == {theirs.public_id, mine.public_id}
    assert await _list_ids(harness, viewer, scope="mine") == [mine.public_id]
    assert await _list_ids(harness, viewer, user="me") == [mine.public_id]
    assert await _list_ids(harness, viewer, scope="shared") == [theirs.public_id]


@pytest.mark.anyio
async def test_viewer_cannot_manage_shares_but_can_leave(harness, people) -> None:
    owner, viewer, member, _ = people
    recording = await harness.add_recording(owner)
    async with harness.session_maker() as session:
        session.add(
            RecordingShare(recording_id=recording.id, user_id=viewer.id, level="edit")
        )
        session.add(
            RecordingShare(
                recording_id=recording.id, group_name="research", level="view"
            )
        )
        await session.commit()
    async with harness.client(viewer) as client:
        listing = await client.get(f"/api/v1/recordings/{recording.public_id}/shares")
        resharing = await client.put(
            f"/api/v1/recordings/{recording.public_id}/shares",
            json={"user_id": member.id, "level": "edit"},
        )
        left = await client.delete(
            f"/api/v1/recordings/{recording.public_id}/shares/me"
        )
    assert listing.status_code == 403
    assert resharing.status_code == 403
    assert left.status_code == 204
    assert recording.public_id not in await _list_ids(harness, viewer)

    # Group members cannot leave one by one.
    async with harness.client(member) as client:
        refused = await client.delete(
            f"/api/v1/recordings/{recording.public_id}/shares/me"
        )
    assert refused.status_code == 409


@pytest.mark.anyio
async def test_owner_removes_a_share(harness, people) -> None:
    owner, viewer, *_ = people
    recording = await harness.add_recording(owner)
    url = f"/api/v1/recordings/{recording.public_id}/shares"
    async with harness.client(owner) as client:
        share_id = (
            await client.put(url, json={"user_id": viewer.id, "level": "view"})
        ).json()[0]["id"]
        remaining = await client.delete(f"{url}/{share_id}")
    assert remaining.status_code == 200 and remaining.json() == []
    async with harness.client(viewer) as client:
        assert (
            await client.get(f"/api/v1/recordings/{recording.public_id}")
        ).status_code == 404


@pytest.mark.anyio
async def test_chat_history_is_personal(harness, people) -> None:
    owner, viewer, *_ = people
    recording = await harness.add_recording(owner)
    async with harness.session_maker() as session:
        session.add(
            RecordingShare(recording_id=recording.id, user_id=viewer.id, level="view")
        )
        session.add(
            ChatMessage(
                recording_id=recording.id,
                user_id=owner.id,
                role="user",
                content="owner's question",
            )
        )
        session.add(
            ChatMessage(
                recording_id=recording.id,
                user_id=viewer.id,
                role="user",
                content="viewer's question",
            )
        )
        await session.commit()
    async with harness.client(viewer) as client:
        history = (
            await client.get(f"/api/v1/transcripts/{recording.public_id}/chat")
        ).json()
        cleared = await client.delete(f"/api/v1/transcripts/{recording.public_id}/chat")
    assert [m["content"] for m in history] == ["viewer's question"]
    assert cleared.status_code == 200
    async with harness.session_maker() as session:
        left = (await session.execute(select(ChatMessage.content))).scalars().all()
    assert left == ["owner's question"]


@pytest.mark.anyio
async def test_tags_are_personal(harness, people) -> None:
    owner, viewer, *_ = people
    recording = await harness.add_recording(owner)
    async with harness.session_maker() as session:
        session.add(
            RecordingShare(recording_id=recording.id, user_id=viewer.id, level="view")
        )
        owner_tag = Tag(name="client-x", user_id=owner.id)
        session.add(owner_tag)
        await session.flush()
        session.add(RecordingTag(recording_id=recording.id, tag_id=owner_tag.id))
        await session.commit()
    async with harness.client(viewer) as client:
        added = await client.post(
            f"/api/v1/tags/recordings/{recording.public_id}", json={"name": "follow-up"}
        )
        assert added.status_code == 200, added.text
        detail = (await client.get(f"/api/v1/recordings/{recording.public_id}")).json()
        listed = (await client.get("/api/v1/recordings/")).json()
    assert [t["name"] for t in detail["tags"]] == ["follow-up"]
    assert [t["name"] for t in listed[0]["tags"]] == ["follow-up"]
    async with harness.client(owner) as client:
        detail = (await client.get(f"/api/v1/recordings/{recording.public_id}")).json()
    assert [t["name"] for t in detail["tags"]] == ["client-x"]


@pytest.mark.anyio
async def test_documents_follow_the_meeting(harness, people, tmp_path) -> None:
    owner, viewer, _, stranger = people
    recording = await harness.add_recording(owner)
    stored = tmp_path / "agenda.txt"
    stored.write_text("agenda")
    async with harness.session_maker() as session:
        session.add(
            RecordingShare(recording_id=recording.id, user_id=viewer.id, level="view")
        )
        document = Document(
            recording_id=recording.id,
            title="agenda.txt",
            file_path=str(stored),
            file_type="text/plain",
            file_size_bytes=6,
            status=DocumentStatus.READY,
        )
        session.add(document)
        await session.commit()
        document_id = document.id
    async with harness.client(viewer) as client:
        listed = await client.get(f"/api/v1/recordings/{recording.public_id}/documents")
        removed = await client.delete(f"/api/v1/documents/{document_id}")
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert removed.status_code == 403
    async with harness.client(stranger) as client:
        assert (
            await client.delete(f"/api/v1/documents/{document_id}")
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/documents/{document_id}/download")
        ).status_code == 404


@pytest.mark.anyio
async def test_user_directory_lists_names_only(harness, people) -> None:
    owner, *_ = people
    async with harness.client(owner) as client:
        users = (await client.get("/api/v1/users/directory")).json()
        groups = (await client.get("/api/v1/users/directory/groups")).json()
    assert {u["username"] for u in users} == {"viewer", "member", "stranger"}
    assert set(users[0]) == {"id", "username", "display_name"}
    assert groups == [{"name": "research", "member_count": 2}]


@pytest.mark.anyio
async def test_sharee_links_and_lists_their_own_tasks_for_a_shared_meeting(
    harness, people
) -> None:
    owner, viewer, _, stranger = people
    recording = await harness.add_recording(owner)
    harness_session = harness.session_maker
    async with harness_session() as session:
        session.add(
            RecordingShare(recording_id=recording.id, user_id=viewer.id, level="view")
        )
        await session.commit()

    async with harness.client(viewer) as client:
        created = await client.post(
            "/api/v1/tasks/",
            json={"title": "Send the slides", "recording_ids": [recording.public_id]},
        )
        listed = await client.get(
            "/api/v1/tasks/", params={"recording_id": recording.public_id}
        )
    assert created.status_code in (200, 201), created.text
    assert [task["title"] for task in listed.json()] == ["Send the slides"]

    async with harness.client(owner) as client:
        owners = await client.get(
            "/api/v1/tasks/", params={"recording_id": recording.public_id}
        )
    assert owners.json() == []
    async with harness.client(stranger) as client:
        refused = await client.get(
            "/api/v1/tasks/", params={"recording_id": recording.public_id}
        )
    assert refused.status_code == 404


@pytest.mark.anyio
async def test_only_a_share_by_name_can_be_left(harness, people) -> None:
    owner, viewer, member, _ = people
    recording = await harness.add_recording(owner)
    async with harness.session_maker() as session:
        session.add(
            RecordingShare(recording_id=recording.id, user_id=viewer.id, level="view")
        )
        session.add(
            RecordingShare(
                recording_id=recording.id, group_name="research", level="view"
            )
        )
        await session.commit()

    async def can_leave(caller) -> tuple[bool, bool]:
        async with harness.client(caller) as client:
            detail = await client.get(f"/api/v1/recordings/{recording.public_id}")
            listed = await client.get("/api/v1/recordings/")
        row = next(r for r in listed.json() if r["id"] == recording.public_id)
        return detail.json()["can_leave"], row["can_leave"]

    assert await can_leave(viewer) == (True, True)
    assert await can_leave(member) == (False, False)
    assert await can_leave(owner) == (False, False)


@pytest.mark.anyio
async def test_search_matches_only_the_searchers_own_tags(harness, people) -> None:
    owner, viewer, *_ = people
    recording = await harness.add_recording(owner)
    async with harness.session_maker() as session:
        session.add(
            RecordingShare(recording_id=recording.id, user_id=viewer.id, level="view")
        )
        tag = Tag(name="Confidential", user_id=owner.id)
        session.add(tag)
        await session.flush()
        session.add(RecordingTag(recording_id=recording.id, tag_id=tag.id))
        await session.commit()

    assert recording.public_id in await _list_ids(harness, owner, q="confid")
    assert recording.public_id not in await _list_ids(harness, viewer, q="confid")
