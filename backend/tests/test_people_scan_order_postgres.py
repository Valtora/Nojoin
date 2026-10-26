"""People scans follow the same recording lock order as global renames."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import Engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import Session, select

from backend.api.v1.endpoints.speakers import routes_global
from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker, GlobalSpeakerUpdate, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.test_canonical_publication_fence import (
    SCHEMA,
    Database,
)
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.utils.canonical_pipeline.constants import _load_transcript


def _scan_speakers(engine: Engine, first_id: int) -> tuple[int, int, int, int]:
    """Create matching speakers in the opposite order to their recordings."""
    with Session(engine) as session:
        first = session.get(Recording, first_id)
        first.pipeline_generation = "unified"
        owner_id = first.user_id
        second = Recording(
            name="Second",
            audio_path="second.wav",
            user_id=owner_id,
            pipeline_generation="unified",
        )
        named = GlobalSpeaker(name="Named", user_id=owner_id, embedding=[0.0, 1.0])
        scanned = GlobalSpeaker(name="Scanned", user_id=owner_id, embedding=[1.0, 0.0])
        session.add_all([second, named, scanned])
        session.flush()
        second_id = second.id
        session.add(Transcript(recording_id=second_id, segments=[]))
        for rid in (first_id, second_id):
            session.add(
                RecordingSpeaker(
                    recording_id=rid,
                    diarization_label="NAMED",
                    global_speaker_id=named.id,
                )
            )
        # Reprocessing older recordings makes speaker ID order oppose recording ID.
        for rid in (second_id, first_id):
            session.add(
                RecordingSpeaker(
                    recording_id=rid,
                    diarization_label="CANDIDATE",
                    embedding=[1.0, 0.0],
                )
            )
        session.commit()
        named_id, scanned_id = named.id, scanned.id
    return owner_id, second_id, named_id, scanned_id


def test_scan_and_global_rename_serialise_in_recording_order(
    publication_database: Database,
) -> None:
    engine, first_id, _ = publication_database
    owner_id, second_id, named_id, scanned_id = _scan_speakers(engine, first_id)

    reaches_first = threading.Event()

    def async_engine() -> Any:
        return create_async_engine(
            engine.url.set(drivername="postgresql+asyncpg"),
            connect_args={
                "server_settings": {
                    "search_path": SCHEMA,
                    "lock_timeout": "3s",
                }
            },
        )

    async def scan() -> dict[str, Any]:
        scan_engine = async_engine()

        def before_lock(
            connection: Any, cursor: Any, statement: str, parameters: Any, *args: Any
        ) -> None:
            if (
                "FROM transcripts" in statement
                and "FOR UPDATE" in statement
                and parameters[0] == first_id
            ):
                reaches_first.set()

        event.listen(scan_engine.sync_engine, "before_cursor_execute", before_lock)
        try:
            async with AsyncSession(scan_engine) as session:
                return await routes_global.scan_for_matches(
                    scanned_id,
                    db=session,
                    current_user=User(
                        id=owner_id, username="fence-owner", hashed_password="x"
                    ),
                )
        finally:
            await scan_engine.dispose()

    async def rename() -> None:
        rename_engine = async_engine()
        try:
            async with AsyncSession(rename_engine) as session:
                await session.execute(text("SET lock_timeout = '1s'"))
                await session.run_sync(
                    lambda sync: _load_transcript(sync, first_id, lock=True)
                )
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(lambda: asyncio.run(scan()))
                    try:
                        assert reaches_first.wait(5), (
                            "Scan never reached the first recording"
                        )
                        await routes_global.update_global_speaker(
                            named_id,
                            GlobalSpeakerUpdate(name="Renamed"),
                            db=session,
                            current_user=User(
                                id=owner_id, username="fence-owner", hashed_password="x"
                            ),
                        )
                    finally:
                        await session.rollback()
                    assert future.result(timeout=10)["matches_found"] == 2
        finally:
            await rename_engine.dispose()

    asyncio.run(rename())
    with Session(engine) as session:
        assert session.get(GlobalSpeaker, named_id).name == "Renamed"
        linked = session.exec(
            select(RecordingSpeaker).where(
                RecordingSpeaker.global_speaker_id == scanned_id,
            )
        ).all()
        assert {row.recording_id for row in linked} == {first_id, second_id}
