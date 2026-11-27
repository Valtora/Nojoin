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
from backend.services.recording_access import (
    INSUFFICIENT_ACCESS_DETAIL,
    NOT_FOUND_DETAIL,
    Access,
    declared_access,
)
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
    ("POST", "/recordings/{recording_id}/shares/me/restore-request"): VIEW,
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
    ): OWNER,
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
    ("POST", "/transcripts/{recording_id}/analytics/ai/generate"): EDIT,
    ("POST", "/transcripts/{recording_id}/analytics/generate"): EDIT,
    ("GET", "/transcripts/{recording_id}/analytics/live"): VIEW,
    ("DELETE", "/transcripts/{recording_id}/chat"): VIEW,
    ("GET", "/transcripts/{recording_id}/chat"): VIEW,
    ("POST", "/transcripts/{recording_id}/chat"): VIEW,
    ("GET", "/transcripts/{recording_id}/export"): VIEW,
    ("PUT", "/transcripts/{recording_id}/meeting-edge-focus"): OWNER_ONLY,
    ("GET", "/transcripts/{recording_id}/notes"): VIEW,
    ("PUT", "/transcripts/{recording_id}/notes"): EDIT,
    ("POST", "/transcripts/{recording_id}/notes/generate"): EDIT,
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


# Path parameters a route checks the form of before its access check: the live
# naming routes take a live label (LIVE_PROVISIONAL_<n>), not a diarization one.
PATH_VALUES = {"label": "LIVE_PROVISIONAL_1"}


def _concrete(path: str, public_id: str) -> str:
    path = path.replace("{recording_id}", public_id)
    return re.sub(
        r"\{([^}]+)\}", lambda match: PATH_VALUES.get(match.group(1), "1"), path
    )


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
    # A handler that runs past its access check may still fail in its own way
    # on these thin fixtures; this test is only about the access check.
    return AsyncClient(
        transport=ASGITransport(app=harness.app, raise_app_exceptions=False),
        base_url="https://localhost:14443",
        headers={"Authorization": f"Bearer {harness.token_for(user)}"},
    )


# A request each route accepts as valid, so that validation (which runs before
# the access check) passes and the call reaches the check. Routes not listed
# take no body and no required query parameter.
_UPLOAD = {"files": {"file": ("agenda.txt", b"Agenda", "text/plain")}}
REQUESTS: dict[tuple[str, str], dict] = {
    ("PATCH", "/recordings/{recording_id}"): {"json": {"name": "Renamed"}},
    ("PUT", "/recordings/{recording_id}/calendar-event"): {"json": {}},
    ("POST", "/recordings/{recording_id}/capture-source-report"): {
        "json": {"attempt_kind": "start", "outcome": "ok", "mode": "microphone"}
    },
    ("PUT", "/recordings/{recording_id}/client_status"): {"params": {"status": "IDLE"}},
    ("POST", "/recordings/{recording_id}/documents"): _UPLOAD,
    ("POST", "/recordings/{recording_id}/reprocess"): {
        "json": {"transcription_backend": "whisper"}
    },
    ("POST", "/recordings/{recording_id}/segment"): {
        **_UPLOAD,
        "params": {"sequence": 0},
    },
    ("PUT", "/recordings/{recording_id}/shares"): {
        "json": {"group": "nobody", "level": "view"}
    },
    ("PUT", "/speakers/recordings/{recording_id}"): {
        "json": {"diarization_label": "SPEAKER_00", "global_speaker_name": "Ann"}
    },
    ("PUT", "/speakers/recordings/{recording_id}/live/{label}"): {
        "json": {"name": "Ann"}
    },
    ("POST", "/speakers/recordings/{recording_id}/live/{label}/merge"): {
        "json": {"into_label": "LIVE_PROVISIONAL_2"}
    },
    ("POST", "/speakers/recordings/{recording_id}/merge"): {
        "json": {
            "target_speaker_label": "SPEAKER_00",
            "source_speaker_label": "SPEAKER_01",
        }
    },
    ("PUT", "/speakers/recordings/{recording_id}/speakers/{label}/color"): {
        "json": {"color": "blue"}
    },
    (
        "POST",
        "/speakers/recordings/{recording_id}/speakers/{diarization_label}/split",
    ): {"json": {"new_speaker_name": "Ann", "segments": []}},
    (
        "POST",
        "/speakers/recordings/{recording_id}/speakers/{diarization_label}/voiceprint/apply",
    ): {"json": {"action": "create_new"}},
    ("POST", "/tags/recordings/{recording_id}"): {"json": {"name": "Planning"}},
    ("POST", "/transcripts/{recording_id}/chat"): {"json": {"message": "Hi"}},
    ("PUT", "/transcripts/{recording_id}/meeting-edge-focus"): {
        "json": {"meeting_edge_focus": "Decisions"}
    },
    ("PUT", "/transcripts/{recording_id}/notes"): {"json": {"notes": "Notes"}},
    ("POST", "/transcripts/{recording_id}/replace"): {
        "json": {"find_text": "a", "replace_text": "b"}
    },
    ("PUT", "/transcripts/{recording_id}/segments"): {"json": {"segments": []}},
    ("PUT", "/transcripts/{recording_id}/segments/{segment_index}"): {
        "json": {"new_speaker_name": "Ann"}
    },
    ("PUT", "/transcripts/{recording_id}/segments/{segment_index}/text"): {
        "json": {"text": "Hello"}
    },
    ("POST", "/transcripts/{recording_id}/timeline-notes"): {
        "json": {"kind": "point", "body": "Note", "anchor_ms": 0}
    },
    ("PATCH", "/transcripts/{recording_id}/timeline-notes/{note_id}"): {
        "json": {"expected_revision": 1, "body": "Note"}
    },
    (
        "POST",
        "/transcripts/{recording_id}/timeline-notes/{note_id}/attachment",
    ): _UPLOAD,
    ("PATCH", "/transcripts/{recording_id}/timeline-notes/{note_id}/attachment"): {
        "json": {"use_in_notes": True}
    },
    ("PATCH", "/transcripts/{recording_id}/utterances/{utterance_id}/speaker"): {
        "json": {"new_speaker_name": "Ann"}
    },
    ("POST", "/transcripts/{recording_id}/utterances/{utterance_id}/split"): {
        "json": {"before_word_id": "w1", "expected_revision": 1}
    },
    ("PATCH", "/transcripts/{recording_id}/utterances/{utterance_id}/text"): {
        "json": {"text": "Hello"}
    },
}


# Routes retired upstream answer 410 Gone to everyone.
RETIRED = {("POST", "/recordings/{recording_id}/upload-token")}


def _detail(response):
    try:
        body = response.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    # The notes routes nest it: {"code": "recording_not_found", "message": ...}.
    if isinstance(detail, dict):
        return detail.get("message")
    return detail


def _refused(response) -> bool:
    """The access check said no: not found, or found but too low a level."""
    detail = _detail(response)
    return (response.status_code == 404 and detail == NOT_FOUND_DETAIL) or (
        response.status_code == 403 and detail == INSUFFICIENT_ACCESS_DETAIL
    )


async def _call(harness, user, method: str, path: str, public_id: str):
    async with _client(harness, user) as client:
        return await client.request(
            method,
            "/api/v1" + _concrete(path, public_id),
            **REQUESTS.get((method, path), {}),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"), sorted(ROUTE_ACCESS), ids=lambda value: str(value)
)
async def test_route_refuses_callers_below_its_level_and_admits_the_rest(
    harness, stub_celery_dispatch, method: str, path: str
) -> None:
    """Each route answers the access check's refusal to everyone below its
    level, and lets a caller at its level past the check. A 422 or an
    unmatched path fails the test, so the check is really reached."""
    expected = ROUTE_ACCESS[(method, path)]
    if (method, path) in RETIRED:
        pytest.skip("retired upstream: 410 for every caller")
    owner = await harness.add_user("owner")
    recording = await harness.add_recording(owner)

    stranger = await harness.add_user("stranger")
    response = await _call(harness, stranger, method, path, recording.public_id)
    assert _refused(response) and response.status_code == 404, response.text

    levels = ["view", "edit"]
    for level in levels:
        sharee = await harness.add_user(f"sharee-{level}")
        await _share(harness, recording, sharee, level)
        response = await _call(harness, sharee, method, path, recording.public_id)
        assert response.status_code != 422, (level, response.text)
        sharee_level = Access.VIEW if level == "view" else Access.EDIT
        if expected == OWNER_ONLY or sharee_level < expected:
            assert _refused(response), (level, response.status_code, response.text)
        else:
            assert not _refused(response), (level, response.status_code, response.text)

    response = await _call(harness, owner, method, path, recording.public_id)
    assert response.status_code != 422, response.text
    assert not _refused(response), (response.status_code, response.text)


# The document routes name a document, not a recording, and take the level of
# the recording it belongs to.
DOCUMENT_ROUTE_ACCESS = {
    ("GET", "/documents/{document_id}/download"): VIEW,
    ("GET", "/documents/{document_id}/preview"): VIEW,
    ("DELETE", "/documents/{document_id}"): EDIT,
    ("POST", "/documents/{document_id}/reparse"): EDIT,
}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"), sorted(DOCUMENT_ROUTE_ACCESS), ids=lambda value: str(value)
)
async def test_document_route_follows_its_recordings_level(
    harness, method: str, path: str
) -> None:
    from backend.models.document import Document, DocumentStatus

    expected = DOCUMENT_ROUTE_ACCESS[(method, path)]
    owner = await harness.add_user("owner")
    recording = await harness.add_recording(owner)
    async with harness.session_maker() as session:
        document = Document(
            recording_id=recording.id,
            title="Agenda",
            file_path="/nonexistent/agenda.txt",
            file_type="text/plain",
            status=DocumentStatus.READY,
        )
        session.add(document)
        await session.commit()
        await session.refresh(document)
    url = "/api/v1" + path.replace("{document_id}", str(document.id))

    stranger = await harness.add_user("stranger")
    async with _client(harness, stranger) as client:
        response = await client.request(method, url)
    assert response.status_code == 404, response.text

    for level, sharee_level in (("view", Access.VIEW), ("edit", Access.EDIT)):
        sharee = await harness.add_user(f"sharee-{level}")
        await _share(harness, recording, sharee, level)
        async with _client(harness, sharee) as client:
            response = await client.request(method, url)
        if sharee_level < expected:
            assert response.status_code == 403, (level, response.text)
        else:
            assert response.status_code not in (403, 422), (level, response.text)
