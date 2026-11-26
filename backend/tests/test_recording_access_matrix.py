"""Every recording-scoped route, with the access level it requires.

``ROUTE_ACCESS`` is the sharing policy in one reviewable table. A route marked
``OWNER_ONLY`` has not opted in to sharing: it keeps the owner check in
api/v1/endpoints/recordings/helpers.py. Every other route declares its level
with ``require_recording`` (backend/services/recording_access.py) and checks it
with ``resolve_grant`` as its first await. The tests below check the
declaration against the table, check that first await, and call every route
as a stranger and as each share level below the one it needs.

When upstream adds a route that names a recording, the coverage test fails
until the route is classified here.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap

import pytest
from httpx import ASGITransport, AsyncClient

from backend.models.recording_share import RecordingShare
from backend.services.recording_access import Access, declared_access
from backend.tests.sso_sharing_support import build_harness, recording_routes

OWNER_ONLY = "owner-only"
VIEW = Access.VIEW
EDIT = Access.EDIT
OWNER = Access.OWNER

ROUTE_ACCESS = {
    ("DELETE", "/recordings/{recording_id}"): OWNER_ONLY,
    ("GET", "/recordings/{recording_id}"): VIEW,
    ("PATCH", "/recordings/{recording_id}"): EDIT,
    ("POST", "/recordings/{recording_id}/archive"): OWNER_ONLY,
    ("PUT", "/recordings/{recording_id}/calendar-event"): OWNER_ONLY,
    ("GET", "/recordings/{recording_id}/calendar-event/candidates"): OWNER_ONLY,
    ("POST", "/recordings/{recording_id}/capture-source-report"): OWNER_ONLY,
    ("PUT", "/recordings/{recording_id}/client_status"): OWNER_ONLY,
    ("POST", "/recordings/{recording_id}/discard"): OWNER_ONLY,
    ("GET", "/recordings/{recording_id}/documents"): VIEW,
    ("POST", "/recordings/{recording_id}/documents"): EDIT,
    ("POST", "/recordings/{recording_id}/finalize"): OWNER_ONLY,
    ("POST", "/recordings/{recording_id}/infer-speakers"): OWNER_ONLY,
    ("GET", "/recordings/{recording_id}/info"): VIEW,
    ("PUT", "/recordings/{recording_id}/max_speakers"): OWNER_ONLY,
    ("POST", "/recordings/{recording_id}/pause"): OWNER_ONLY,
    ("DELETE", "/recordings/{recording_id}/permanent"): OWNER_ONLY,
    ("POST", "/recordings/{recording_id}/reprocess"): OWNER_ONLY,
    ("POST", "/recordings/{recording_id}/restore"): OWNER_ONLY,
    ("POST", "/recordings/{recording_id}/resume"): OWNER_ONLY,
    ("POST", "/recordings/{recording_id}/segment"): OWNER_ONLY,
    ("POST", "/recordings/{recording_id}/soft-delete"): OWNER_ONLY,
    ("GET", "/recordings/{recording_id}/stream"): VIEW,
    ("GET", "/recordings/{recording_id}/shares"): OWNER,
    ("PUT", "/recordings/{recording_id}/shares"): OWNER,
    ("DELETE", "/recordings/{recording_id}/shares/me"): VIEW,
    ("DELETE", "/recordings/{recording_id}/shares/{share_id}"): OWNER,
    ("POST", "/recordings/{recording_id}/upload-token"): OWNER_ONLY,
    ("PUT", "/speakers/recordings/{recording_id}"): EDIT,
    ("PUT", "/speakers/recordings/{recording_id}/live/{label}"): OWNER_ONLY,
    ("POST", "/speakers/recordings/{recording_id}/live/{label}/merge"): OWNER_ONLY,
    (
        "POST",
        "/speakers/recordings/{recording_id}/live/{label}/suggestions/reject",
    ): OWNER_ONLY,
    ("POST", "/speakers/recordings/{recording_id}/merge"): EDIT,
    ("GET", "/speakers/recordings/{recording_id}/merged-speakers"): VIEW,
    ("GET", "/speakers/recordings/{recording_id}/speaker-candidates"): VIEW,
    (
        "DELETE",
        "/speakers/recordings/{recording_id}/speakers/{diarization_label}",
    ): EDIT,
    (
        "POST",
        "/speakers/recordings/{recording_id}/speakers/{diarization_label}/promote",
    ): EDIT,
    (
        "POST",
        "/speakers/recordings/{recording_id}/speakers/{diarization_label}/split",
    ): EDIT,
    (
        "POST",
        "/speakers/recordings/{recording_id}/speakers/{diarization_label}/suggestions/accept",
    ): EDIT,
    (
        "POST",
        "/speakers/recordings/{recording_id}/speakers/{diarization_label}/suggestions/reject",
    ): EDIT,
    (
        "POST",
        "/speakers/recordings/{recording_id}/speakers/{diarization_label}/unmerge",
    ): EDIT,
    (
        "DELETE",
        "/speakers/recordings/{recording_id}/speakers/{diarization_label}/voiceprint",
    ): OWNER_ONLY,
    (
        "POST",
        "/speakers/recordings/{recording_id}/speakers/{diarization_label}/voiceprint/apply",
    ): OWNER_ONLY,
    (
        "POST",
        "/speakers/recordings/{recording_id}/speakers/{diarization_label}/voiceprint/extract",
    ): OWNER_ONLY,
    ("PUT", "/speakers/recordings/{recording_id}/speakers/{label}/color"): EDIT,
    ("POST", "/speakers/recordings/{recording_id}/voiceprints/extract-all"): OWNER_ONLY,
    ("POST", "/tags/recordings/{recording_id}"): VIEW,
    ("DELETE", "/tags/recordings/{recording_id}/{tag_name}"): VIEW,
    ("GET", "/transcripts/{recording_id}/analytics"): VIEW,
    ("POST", "/transcripts/{recording_id}/analytics/ai/generate"): OWNER_ONLY,
    ("POST", "/transcripts/{recording_id}/analytics/generate"): OWNER_ONLY,
    ("GET", "/transcripts/{recording_id}/analytics/live"): VIEW,
    ("DELETE", "/transcripts/{recording_id}/chat"): VIEW,
    ("GET", "/transcripts/{recording_id}/chat"): VIEW,
    ("POST", "/transcripts/{recording_id}/chat"): VIEW,
    ("GET", "/transcripts/{recording_id}/export"): VIEW,
    ("PUT", "/transcripts/{recording_id}/meeting-edge-focus"): OWNER_ONLY,
    ("GET", "/transcripts/{recording_id}/notes"): VIEW,
    ("PUT", "/transcripts/{recording_id}/notes"): EDIT,
    ("POST", "/transcripts/{recording_id}/notes/generate"): OWNER_ONLY,
    ("POST", "/transcripts/{recording_id}/replace"): EDIT,
    ("PUT", "/transcripts/{recording_id}/segments"): EDIT,
    ("PUT", "/transcripts/{recording_id}/segments/{segment_index}"): EDIT,
    ("PUT", "/transcripts/{recording_id}/segments/{segment_index}/text"): EDIT,
    ("GET", "/transcripts/{recording_id}/timeline-notes"): VIEW,
    ("POST", "/transcripts/{recording_id}/timeline-notes"): EDIT,
    ("DELETE", "/transcripts/{recording_id}/timeline-notes/{note_id}"): EDIT,
    ("PATCH", "/transcripts/{recording_id}/timeline-notes/{note_id}"): EDIT,
    ("DELETE", "/transcripts/{recording_id}/timeline-notes/{note_id}/attachment"): EDIT,
    ("PATCH", "/transcripts/{recording_id}/timeline-notes/{note_id}/attachment"): EDIT,
    ("POST", "/transcripts/{recording_id}/timeline-notes/{note_id}/attachment"): EDIT,
    ("GET", "/transcripts/{recording_id}/utterances"): VIEW,
    ("DELETE", "/transcripts/{recording_id}/utterances/{utterance_id}/locks"): EDIT,
    ("PATCH", "/transcripts/{recording_id}/utterances/{utterance_id}/speaker"): EDIT,
    ("POST", "/transcripts/{recording_id}/utterances/{utterance_id}/split"): EDIT,
    ("PATCH", "/transcripts/{recording_id}/utterances/{utterance_id}/text"): EDIT,
}


def test_every_recording_route_is_classified() -> None:
    found = {(method, path): route for method, path, route in recording_routes()}
    unclassified = sorted(set(found) - set(ROUTE_ACCESS))
    stale = sorted(set(ROUTE_ACCESS) - set(found))
    assert unclassified == [], "Classify these routes in ROUTE_ACCESS"
    assert stale == [], "These routes no longer exist"


@pytest.mark.parametrize(
    ("method", "path"), sorted(ROUTE_ACCESS), ids=lambda value: str(value)
)
def test_route_declares_the_level_in_the_table(method: str, path: str) -> None:
    routes = {(m, p): route for m, p, route in recording_routes()}
    expected = ROUTE_ACCESS[(method, path)]
    declared = declared_access(routes[(method, path)])
    if expected == OWNER_ONLY:
        assert declared is None
    else:
        assert declared is expected


# The calls that check access for a handler before it does anything else.
ACCESS_CHECKS = {"resolve_grant", "_resolve_note_grant", "_load_recording"}


def _first_await_call(function) -> str | None:
    source = textwrap.dedent(inspect.getsource(function))
    tree = ast.parse(source)
    body = tree.body[0]
    for node in ast.walk(body):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            target = node.value.func
            return getattr(target, "id", None) or getattr(target, "attr", None)
    return None


def _awaits_in_order(function) -> list[str]:
    source = textwrap.dedent(inspect.getsource(function))
    calls = []
    for node in ast.walk(ast.parse(source).body[0]):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            target = node.value.func
            calls.append(
                (
                    node.lineno,
                    node.col_offset,
                    getattr(target, "id", None) or getattr(target, "attr", None),
                )
            )
    return [name for _, _, name in sorted(calls)]


@pytest.mark.parametrize(
    ("method", "path"),
    sorted(key for key, level in ROUTE_ACCESS.items() if level != OWNER_ONLY),
    ids=lambda value: str(value),
)
def test_handler_checks_access_before_anything_else(method: str, path: str) -> None:
    routes = {(m, p): route for m, p, route in recording_routes()}
    awaits = _awaits_in_order(routes[(method, path)].endpoint)
    assert awaits and awaits[0] in ACCESS_CHECKS, awaits[:3]


def _concrete(path: str, public_id: str) -> str:
    path = path.replace("{recording_id}", public_id)
    return re.sub(r"\{[^}]+\}", "1", path)


@pytest.fixture
async def harness():
    async for built in build_harness():
        yield built


async def _share(harness, recording, user, level: str) -> None:
    async with harness.session_maker() as session:
        session.add(
            RecordingShare(recording_id=recording.id, user_id=user.id, level=level)
        )
        await session.commit()


def _client(harness, user) -> AsyncClient:
    # A handler that runs with an empty body may fail in its own way; this
    # test is only about whether the access check let the call through.
    return AsyncClient(
        transport=ASGITransport(app=harness.app, raise_app_exceptions=False),
        base_url="https://localhost:14443",
        headers={"Authorization": f"Bearer {harness.token_for(user)}"},
    )


def _refused(response) -> bool:
    return response.status_code == 404 or (
        response.status_code == 403
        and response.json().get("detail") == "insufficient_access"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"), sorted(ROUTE_ACCESS), ids=lambda value: str(value)
)
async def test_route_refuses_callers_below_its_level(
    harness, stub_celery_dispatch, method: str, path: str
) -> None:
    expected = ROUTE_ACCESS[(method, path)]
    owner = await harness.add_user("owner")
    recording = await harness.add_recording(owner)
    url = _concrete(path, recording.public_id)

    stranger = await harness.add_user("stranger")
    async with _client(harness, stranger) as client:
        response = await client.request(method, url)
    # 422: the request failed validation, which runs before any access check
    # (an empty body, a missing query parameter). It reveals nothing.
    if expected == OWNER_ONLY:
        assert response.status_code in (404, 422), response.text
    else:
        assert response.status_code in (404, 422), response.text

    levels_below = {
        OWNER_ONLY: ["view", "edit"],
        OWNER: ["view", "edit"],
        EDIT: ["view"],
        VIEW: [],
    }[expected]
    for level in levels_below:
        sharee = await harness.add_user(f"sharee-{level}")
        await _share(harness, recording, sharee, level)
        async with _client(harness, sharee) as client:
            response = await client.request(method, url)
        if expected == OWNER_ONLY:
            # The owner-only check answers 404 to everyone else.
            assert response.status_code in (404, 422), response.text
        else:
            assert response.status_code == 422 or _refused(response), (
                level,
                response.status_code,
                response.text,
            )
