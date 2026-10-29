"""Resolving pending state changes line membership without changing word identity."""

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from backend.tests.test_live_settling_pending import _enable_speakers
from backend.tests.test_live_settling_publication import _request, _seed, _words
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline.core import serialize_canonical_utterances
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)
from backend.utils.canonical_pipeline.live_settling import publish_live_settling
from backend.utils.canonical_pipeline.projection import serialize_canonical_delta


@pytest.mark.parametrize("speaker", ["LIVE_PROVISIONAL_1", "LIVE_PROVISIONAL_2"])
def test_resolved_pending_word_merges_or_splits_with_its_real_speaker(
    settling_engine: Engine,
    speaker: str,
) -> None:
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)

        def initial(start: int, _end: int) -> LiveLabelDecision:
            return (
                LiveLabelDecision("LIVE_PROVISIONAL_1", False, 1.0)
                if start < 500
                else LiveLabelDecision("UNKNOWN", False, None, True)
            )

        publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(),
            decide_speaker=initial,
        )
        session.commit()
        cursor, before, _ = serialize_canonical_delta(session, recording_id)
        assert [line["speaker"] for line in before] == ["LIVE_PROVISIONAL_1", "UNKNOWN"]

        def resolved(start: int, _end: int) -> LiveLabelDecision:
            return LiveLabelDecision(
                "LIVE_PROVISIONAL_1" if start < 500 else speaker, False, 1.0
            )

        apply_live_speaker_labels(
            session, recording_id=recording_id, settled_ms=0, decide=resolved
        )
        session.commit()
        after = serialize_canonical_utterances(session, recording_id)
        _, changed, tombstones = serialize_canonical_delta(
            session, recording_id, after_revision=cursor
        )
        assert [word["id"] for line in after for word in line["words"]] == [
            word["id"] for line in before for word in line["words"]
        ]
        assert all(line["diarisation_pending"] is False for line in after)
        if speaker == "LIVE_PROVISIONAL_1":
            assert len(after) == len(changed) == 1
            assert set(tombstones) == {line["id"] for line in before}
        else:
            assert [line["speaker"] for line in after] == [
                "LIVE_PROVISIONAL_1",
                speaker,
            ]
            assert len(changed) == 1 and not tombstones
