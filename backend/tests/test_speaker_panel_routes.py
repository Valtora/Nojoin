"""The panel reuses whole-speaker naming and explicit People promotion."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from backend.tests.test_library_voiceprint_api import (
    REC,
    _seed,
)
from backend.tests.test_library_voiceprint_api import (
    client as client,
)
from backend.tests.test_library_voiceprint_api import (
    session_maker as session_maker,
)
from backend.tests.test_library_voiceprint_api import (
    write_path as write_path,
)


@pytest.mark.anyio
async def test_whole_speaker_identity_links_existing_person_without_changing_other_speaker(
    client: AsyncClient,
    session_maker: Any,
    write_path: str,
) -> None:
    await _seed(
        session_maker,
        speakers=[
            {"label": "SPEAKER_00", "local_name": "Alex"},
            {"label": "SPEAKER_01", "local_name": "Blair"},
        ],
        people=[{"id": 7, "name": "Robin"}],
    )
    response = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_00", "global_speaker_name": "Robin"},
    )
    assert response.status_code == 200
    speakers = {row["diarization_label"]: row for row in response.json()}
    assert speakers["SPEAKER_00"]["global_speaker_id"] == 7
    async with session_maker() as session:
        # Rename returns the affected speaker, so check the other row in storage.
        assert (
            await session.execute(
                text(
                    "SELECT local_name FROM recording_speakers WHERE diarization_label = 'SPEAKER_01'"
                )
            )
        ).scalar_one() == "Blair"
        assert (
            await session.execute(text("SELECT count(*) FROM global_speakers"))
        ).scalar_one() == 1


@pytest.mark.anyio
async def test_visible_promotion_still_rejects_placeholder_until_renamed(
    client: AsyncClient,
    session_maker: Any,
    write_path: str,
) -> None:
    await _seed(session_maker, speakers=[{"label": "SPEAKER_00"}])
    route = f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/promote"
    refused = await client.post(route)
    assert refused.status_code == 400
    assert "placeholder" in refused.json()["detail"]
    renamed = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": "SPEAKER_00", "global_speaker_name": "Robin"},
    )
    assert renamed.status_code == 200
    async with session_maker() as session:
        assert (
            await session.execute(text("SELECT count(*) FROM global_speakers"))
        ).scalar_one() == 0
    promoted = await client.post(route)
    assert promoted.status_code == 200
    assert promoted.json()["global_speaker_id"] is not None
