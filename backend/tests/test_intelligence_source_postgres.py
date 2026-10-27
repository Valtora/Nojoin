"""Derived products validate their source while holding the publication fence."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from backend.tests.test_canonical_publication_fence import Database
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.worker.tasks.intelligence_source import (
    intelligence_source_is_current,
    load_intelligence_source,
)


def test_source_validation_waits_and_rejects_the_preceding_publication(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    waiting = threading.Event()

    def entering_fence(
        connection: Any, cursor: Any, statement: str, *args: Any
    ) -> None:
        if "FROM transcripts" in statement and "FOR UPDATE" in statement:
            waiting.set()

    with Session(engine, expire_on_commit=False) as writer:
        old = load_intelligence_source(writer, recording_id)
        assert old is not None
        revision = old.change_sequence
        writer.commit()
        with Session(engine) as publisher:
            replacement = _load_transcript(publisher, recording_id, lock=True)
            assert replacement is not None
            replacement.text = "Replacement words"
            replacement.notes = "Replacement notes"
            publisher.add(replacement)
            publisher.flush()
            assert replacement.change_sequence > revision
            event.listen(engine, "before_cursor_execute", entering_fence)
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        intelligence_source_is_current, writer, recording_id, revision
                    )
                    try:
                        assert waiting.wait(5), "Writer never acquired the source fence"
                        assert not future.done(), "Validation bypassed the publisher"
                    finally:
                        publisher.commit()
                    assert future.result(timeout=5) is False
            finally:
                event.remove(engine, "before_cursor_execute", entering_fence)
        # The writer had an unexpired old ORM row before validation waited.
        assert old.notes == "Replacement notes"
        assert old.text == "Replacement words"


def test_current_source_holds_the_fence_until_the_product_commit(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as writer, Session(engine) as publisher:
        source = load_intelligence_source(writer, recording_id)
        assert source is not None
        revision = source.change_sequence
        writer.commit()
        assert intelligence_source_is_current(writer, recording_id, revision)
        publisher.execute(text("SET LOCAL lock_timeout = '100ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            _load_transcript(publisher, recording_id, lock=True)
        publisher.rollback()
        source.notes = "Current source notes"
        writer.add(source)
        writer.commit()
        # Product writes do not invalidate their own transcript source token.
        published = _load_transcript(publisher, recording_id, lock=True)
        assert published is not None
        assert published.notes == "Current source notes"
        assert published.change_sequence == revision
