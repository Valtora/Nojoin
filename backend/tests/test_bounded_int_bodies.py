"""Integer fields of request bodies stay inside the range Postgres stores (#927).

The sibling of ``test_bounded_int_params.py`` for bodies. A tag id past BIGINT in
a task's ``tag_ids``, or an invitation's ``max_uses`` past INTEGER, reached
Postgres and answered 500. Row ids in a body now take ``RowId`` and counters the
bounds of their column, so such a value gets the 422 any other invalid field
gets, and a guard fails on a new integer body field left without both bounds.

Alice (user 1) is signed in. Every name here is made up.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, get_args

import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from backend.api.v1.api import api_router
from backend.tests.test_bounded_int_params import (
    BIGINT_MAX,
    ID_OVER,
    INTEGER_MAX,
    PAGE_OVER,
    TOO_LARGE,
    TOO_SMALL,
    _api_routes,
    _bounded,
    _dependants,
    _mentions_int,
)
from backend.tests.test_bounded_int_params import client as client
from backend.tests.test_bounded_int_params import postgres_client as postgres_client

pytestmark = pytest.mark.anyio

# The longest an invitation may last, a century, written out like the maxima.
MAX_INVITATION_DAYS = 36_500


def _body(
    method: str, url: str, body: dict[str, Any], loc: list[str | int], kind: str
) -> Any:
    field = ".".join(str(part) for part in loc)
    return pytest.param(
        method, url, body, ["body", *loc], kind, id=f"{method} {url} {field} {kind}"
    )


OUT_OF_RANGE = [
    # The issue's list: row ids in tasks, people, chat, tags and calendars.
    _body(
        "POST",
        "/tasks/",
        {"title": "Send the notes", "tag_ids": [ID_OVER]},
        ["tag_ids", 0],
        TOO_LARGE,
    ),
    _body("PATCH", "/tasks/1", {"tag_ids": [3, 0]}, ["tag_ids", 1], TOO_SMALL),
    _body(
        "POST",
        "/speakers/",
        {"name": "Bob Example", "tag_ids": [ID_OVER]},
        ["tag_ids", 0],
        TOO_LARGE,
    ),
    _body("PUT", "/speakers/1", {"tag_ids": [ID_OVER]}, ["tag_ids", 0], TOO_LARGE),
    _body(
        "POST",
        "/transcripts/rec-1/chat",
        {"message": "What was agreed?", "tag_ids": [ID_OVER]},
        ["tag_ids", 0],
        TOO_LARGE,
    ),
    _body(
        "PUT",
        "/calendar/connections/1/calendars",
        {"selected_calendar_ids": [ID_OVER]},
        ["selected_calendar_ids", 0],
        TOO_LARGE,
    ),
    _body(
        "POST",
        "/tags/",
        {"name": "Budget", "parent_id": ID_OVER},
        ["parent_id"],
        TOO_LARGE,
    ),
    _body(
        "POST",
        "/tags/recordings/rec-1",
        {"name": "Budget", "parent_id": ID_OVER},
        ["parent_id"],
        TOO_LARGE,
    ),
    _body("PATCH", "/tags/1", {"parent_id": 0}, ["parent_id"], TOO_SMALL),
    _body(
        "POST",
        "/people-tags/",
        {"name": "Finance", "parent_id": ID_OVER},
        ["parent_id"],
        TOO_LARGE,
    ),
    _body("PATCH", "/people-tags/1", {"parent_id": ID_OVER}, ["parent_id"], TOO_LARGE),
    _body(
        "POST",
        "/speakers/merge",
        {"source_speaker_id": ID_OVER, "target_speaker_id": 1},
        ["source_speaker_id"],
        TOO_LARGE,
    ),
    _body(
        "POST",
        "/speakers/merge",
        {"source_speaker_id": 1, "target_speaker_id": 0},
        ["target_speaker_id"],
        TOO_SMALL,
    ),
    _body(
        "PUT",
        "/recordings/rec-1/calendar-event",
        {"calendar_event_id": ID_OVER},
        ["calendar_event_id"],
        TOO_LARGE,
    ),
    _body(
        "POST",
        "/transcripts/rec-1/notes/generate",
        {"notes_template_id": ID_OVER},
        ["notes_template_id"],
        TOO_LARGE,
    ),
    # The person a transcript line, a whole speaker or a voiceprint links to.
    _body(
        "PUT",
        "/transcripts/rec-1/segments/0",
        {"new_speaker_name": "Alice Example", "global_speaker_id": ID_OVER},
        ["global_speaker_id"],
        TOO_LARGE,
    ),
    _body(
        "PATCH",
        "/transcripts/rec-1/utterances/utt-1/speaker",
        {"new_speaker_name": "Alice Example", "global_speaker_id": ID_OVER},
        ["global_speaker_id"],
        TOO_LARGE,
    ),
    _body(
        "POST",
        "/speakers/recordings/rec-1/speakers/SPEAKER_00/voiceprint/apply",
        {"action": "link_existing", "global_speaker_id": ID_OVER},
        ["global_speaker_id"],
        TOO_LARGE,
    ),
    # Found by the guard below: notes structure defaults are row ids too, and
    # three counters are INTEGER columns or a date.
    _body(
        "POST",
        "/settings/",
        {"notes_template_id": ID_OVER},
        ["notes_template_id"],
        TOO_LARGE,
    ),
    _body(
        "POST",
        "/settings/",
        {"install_notes_template_id": 0},
        ["install_notes_template_id"],
        TOO_SMALL,
    ),
    _body("POST", "/invitations/", {"max_uses": PAGE_OVER}, ["max_uses"], TOO_LARGE),
    _body("POST", "/invitations/", {"max_uses": -1}, ["max_uses"], TOO_SMALL),
    _body(
        "POST",
        "/invitations/",
        {"expires_in_days": MAX_INVITATION_DAYS + 1},
        ["expires_in_days"],
        TOO_LARGE,
    ),
    _body(
        "PATCH",
        "/users/1",
        {"ai_analysis_daily_limit": PAGE_OVER},
        ["ai_analysis_daily_limit"],
        TOO_LARGE,
    ),
]


@pytest.mark.parametrize(("method", "url", "body", "loc", "kind"), OUT_OF_RANGE)
async def test_an_out_of_range_body_integer_is_refused_in_validation(  # noqa: PLR0913 - one fixture, five parametrize axes
    client: AsyncClient,
    method: str,
    url: str,
    body: dict[str, Any],
    loc: list[Any],
    kind: str,
) -> None:
    response = await client.request(method, f"/api/v1{url}", json=body)

    assert response.status_code == 422, response.text
    refusals = [error for error in response.json()["detail"] if error["loc"] == loc]
    assert len(refusals) == 1, response.json()
    assert refusals[0]["type"] == kind


async def test_the_refusal_reads_like_any_other_invalid_field(
    client: AsyncClient,
) -> None:
    """The same 422 body FastAPI gives a body value that is not an integer."""
    too_large = await client.post(
        "/api/v1/tasks/", json={"title": "Send the notes", "tag_ids": [ID_OVER]}
    )
    not_a_number = await client.post(
        "/api/v1/tasks/", json={"title": "Send the notes", "tag_ids": ["many"]}
    )

    assert too_large.status_code == not_a_number.status_code == 422
    [error] = too_large.json()["detail"]
    assert error == {
        "type": "less_than_equal",
        "loc": ["body", "tag_ids", 0],
        "msg": f"Input should be less than or equal to {BIGINT_MAX}",
        "input": ID_OVER,
        "ctx": {"le": BIGINT_MAX},
    }
    [other] = not_a_number.json()["detail"]
    assert set(other) <= set(error)


# Integer body fields left without a floor and a ceiling, each with its reason.
# A new one takes RowId, or bounds of its own, or an entry here.
BODY_UNBOUNDED = {
    # Compared with the stored revision in Python, never bound to SQL.
    "transcripts.helpers:TranscriptUtteranceSpeakerPatch.expected_revision",
    "transcripts.helpers:TranscriptUtteranceTextPatch.expected_revision",
    "transcripts.helpers:TranscriptUtteranceSplit.expected_revision",
    "models.timeline_note_public:TimelineNoteUpdate.expected_revision",
    # Inherited from BaseDBModel and never read: Postgres numbers the person.
    "models.speaker:GlobalSpeakerCreate.id",
    "models.speaker:GlobalSpeakerUpdate.id",
    # Read only for whether it is set: the route stamps the shipped version.
    "models.notes_template:NotesTemplateCreate.builtin_version",
    # Range-checked by their validators, which the walk cannot see.
    "settings:SettingsUpdate.meeting_edge_context_level",
    "settings:SettingsUpdate.phantom_max_segments",
    # Install-wide, kept in the config file and read in Python, never bound to
    # SQL: the AI analysis cap is compared with a count, and the context
    # windows go to Ollama.
    "settings:SettingsUpdate.ai_analysis_daily_limit",
    "settings:SettingsUpdate.ollama_context_window",
    "settings:SettingsUpdate.secondary_ollama_context_window",
}


def _models(annotation: Any) -> Iterator[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
    for arg in get_args(annotation):
        yield from _models(arg)


def _model_fields(
    model: type[BaseModel], seen: set[type[BaseModel]]
) -> Iterator[tuple[str, FieldInfo]]:
    """Each field of a body model, and of the models nested in it."""
    if model in seen:
        return
    seen.add(model)
    module = model.__module__.removeprefix("backend.api.v1.endpoints.")
    module = module.removeprefix("backend.")
    for name, field in model.model_fields.items():
        yield f"{module}:{model.__qualname__}.{name}", field
        for nested in _models(field.annotation):
            yield from _model_fields(nested, seen)


def _integer_body_fields() -> dict[str, bool]:
    """Each integer field a request body can carry, and whether it is bounded."""
    found: dict[str, bool] = {}
    for route in _api_routes(api_router.routes):
        module = route.endpoint.__module__.removeprefix("backend.api.v1.endpoints.")
        for dependant in _dependants(route.dependant):
            for param in dependant.body_params:
                info = param.field_info
                fields = [(f"{module}.{route.endpoint.__name__}:{param.name}", info)]
                for model in _models(info.annotation):
                    fields.extend(_model_fields(model, set()))
                for key, field in fields:
                    if _mentions_int(field.annotation):
                        found[key] = _bounded(field.annotation, field.metadata)
    return found


def test_every_integer_body_field_is_bounded() -> None:
    found = _integer_body_fields()
    # The walk found the bodies, nested and behind a dependency, so an empty
    # result cannot pass by accident.
    assert found["models.task:UserTaskCreate.tag_ids"] is True
    assert found["settings:SettingsUpdate.notes_template_id"] is True
    assert found["models.timeline_note_public:TimelineNoteCreate.start_ms"] is True

    unbounded = {key for key, bounded in found.items() if not bounded}
    assert unbounded - BODY_UNBOUNDED == set(), (
        "bound these, or list them in BODY_UNBOUNDED"
    )
    assert BODY_UNBOUNDED - unbounded == set(), "these are bounded now: drop them"


# Against a real server: a row at the very top of the BIGINT range is found
# through a body, and a counter at the top of INTEGER is stored.


@pytest.mark.parametrize(
    ("method", "url", "body", "status"),
    [
        ("POST", "/tags/", {"name": "Budget", "parent_id": BIGINT_MAX}, 200),
        ("POST", "/people-tags/", {"name": "Board", "parent_id": BIGINT_MAX}, 200),
        ("PATCH", "/tasks/1", {"tag_ids": [BIGINT_MAX]}, 200),
        ("POST", "/speakers/", {"name": "Bob Example", "tag_ids": [BIGINT_MAX]}, 200),
        ("PUT", f"/speakers/{BIGINT_MAX}", {"tag_ids": [BIGINT_MAX]}, 200),
        # Both people found, then refused for being the same one.
        (
            "POST",
            "/speakers/merge",
            {"source_speaker_id": BIGINT_MAX, "target_speaker_id": BIGINT_MAX},
            400,
        ),
        # Looked up and not found, rather than refused by Postgres.
        (
            "PUT",
            "/recordings/rec-1/calendar-event",
            {"calendar_event_id": BIGINT_MAX},
            404,
        ),
        (
            "POST",
            "/invitations/",
            {"expires_in_days": MAX_INVITATION_DAYS, "max_uses": INTEGER_MAX},
            200,
        ),
        ("PATCH", "/users/1", {"ai_analysis_daily_limit": INTEGER_MAX}, 200),
    ],
)
async def test_postgres_accepts_every_body_value_up_to_the_bound(
    postgres_client: AsyncClient,
    method: str,
    url: str,
    body: dict[str, Any],
    status: int,
) -> None:
    response = await postgres_client.request(method, f"/api/v1{url}", json=body)

    assert response.status_code == status, response.text


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("POST", "/tags/", {"name": "Budget", "parent_id": ID_OVER}),
        ("PATCH", "/tasks/1", {"tag_ids": [ID_OVER]}),
        (
            "POST",
            "/speakers/merge",
            {"source_speaker_id": ID_OVER, "target_speaker_id": 1},
        ),
        ("POST", "/invitations/", {"max_uses": PAGE_OVER}),
        ("PATCH", "/users/1", {"ai_analysis_daily_limit": PAGE_OVER}),
    ],
)
async def test_postgres_never_sees_a_body_value_past_the_bound(
    postgres_client: AsyncClient, method: str, url: str, body: dict[str, Any]
) -> None:
    response = await postgres_client.request(method, f"/api/v1{url}", json=body)

    assert response.status_code == 422, response.text
