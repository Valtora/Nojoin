"""Merging one live speaker into another during capture, through the API (#775).

The lines are ``live_naming_fixtures.LIVE_LINES``: live labels ONE, TWO and
THREE. A merge is the user's event on the merged label; the live read then
shows its lines as the label it joined, and the final pass makes them one.
"""

from __future__ import annotations

import pytest

from backend.tests.live_naming_fixtures import ONE, THREE, TWO
from backend.tests.test_live_speaker_naming_api import REC, _events, _name, _seed
from backend.tests.test_live_speaker_naming_api import client as client
from backend.tests.test_live_speaker_naming_api import session_maker as session_maker

pytestmark = pytest.mark.anyio


async def _merge(client, label: str, into: str):
    return await client.post(
        f"/api/v1/speakers/recordings/{REC}/live/{label}/merge",
        json={"into_label": into},
    )


async def test_a_merge_reads_as_the_label_joined_on_every_read(
    client, session_maker
) -> None:
    await _seed(session_maker)
    assert (await _name(client, THREE, "Bea Sample")).status_code == 200

    response = await _merge(client, THREE, ONE)

    assert response.status_code == 200, response.text
    # ONE has no name, so THREE's lines read as "Speaker 1"; Bea is gone.
    assert response.json() == {
        "live_speaker_names": {THREE: "Speaker 1"},
        "live_speaker_merges": {THREE: ONE},
    }
    events = await _events(session_maker)
    assert [
        (e["kind"], e["anchor_label"], e["supersedes_event_id"]) for e in events
    ] == [
        ("name", THREE, None),
        ("merge", THREE, 1),
    ]
    full = await client.get(f"/api/v1/transcripts/{REC}/utterances")
    revision = full.json()["revision"]
    # A merge changes no line, so a delta with nothing new still carries it.
    delta = await client.get(
        f"/api/v1/transcripts/{REC}/utterances", params={"after_revision": revision}
    )
    for read in (full.json(), delta.json()):
        assert read["live_speaker_merges"] == {THREE: ONE}
        assert read["live_speaker_names"] == {THREE: "Speaker 1"}


async def test_naming_a_merged_label_names_the_speaker_it_joined(
    client, session_maker
) -> None:
    await _seed(session_maker)
    assert (await _merge(client, THREE, ONE)).status_code == 200

    response = await _name(client, THREE, "Alex Example")

    assert response.json() == {
        "live_speaker_names": {ONE: "Alex Example", THREE: "Alex Example"},
        "live_speaker_merges": {THREE: ONE},
    }
    assert (await _events(session_maker))[-1]["anchor_label"] == ONE


async def test_a_merged_group_moves_whole(client, session_maker) -> None:
    await _seed(session_maker)
    assert (await _merge(client, THREE, ONE)).status_code == 200

    response = await _merge(client, ONE, TWO)

    assert response.json()["live_speaker_merges"] == {THREE: TWO, ONE: TWO}


@pytest.mark.parametrize(
    ("label", "into", "refusal"),
    [
        pytest.param(
            ONE, ONE, (422, "These lines already belong to one speaker"), id="itself"
        ),
        pytest.param(
            "LIVE_PROVISIONAL_9",
            ONE,
            (404, "Live speaker not found in this recording"),
            id="unheard",
        ),
        pytest.param(
            ONE,
            "SPEAKER_00",
            (404, "Live speaker not found in this recording"),
            id="not-live",
        ),
    ],
)
async def test_a_merge_that_is_no_merge_is_refused(
    client, session_maker, label, into, refusal
) -> None:
    await _seed(session_maker)

    response = await _merge(client, label, into)

    assert (response.status_code, response.json()["detail"]) == refusal, response.text
    assert await _events(session_maker) == []


async def test_merging_back_into_its_own_group_is_refused(
    client, session_maker
) -> None:
    await _seed(session_maker)
    assert (await _merge(client, THREE, ONE)).status_code == 200

    response = await _merge(client, ONE, THREE)

    assert response.status_code == 422
    assert len(await _events(session_maker)) == 1


@pytest.mark.parametrize(
    ("status", "merged"),
    [
        ("UPLOADING", True),
        ("PAUSED", True),
        ("PROCESSING", False),
        ("PROCESSED", False),
    ],
)
async def test_merges_are_taken_only_while_capturing_or_paused(
    client, session_maker, status, merged
) -> None:
    await _seed(session_maker, status=status)

    response = await _merge(client, THREE, ONE)

    assert response.status_code == (200 if merged else 409), response.text
    assert len(await _events(session_maker)) == (1 if merged else 0)
