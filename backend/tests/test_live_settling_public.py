"""Public reads retain word locks but never expose comparison evidence."""

from typing import Any

from backend.models.live_settling_public import public_settling_fields
from backend.models.recording_public import serialize_transcript
from backend.models.transcript import Transcript


def _payload() -> dict[str, Any]:
    return {
        "live_text_settling": {
            "version": 1,
            "generation": "private",
            "line_locked": True,
        },
        "asr_segments": [
            {
                "words": [
                    {
                        "id": "word-1",
                        "order": 1,
                        "word": "Hello!",
                        "start_ms": 100,
                        "end_ms": 300,
                        "source_start": 0,
                        "source_end": 6,
                        "locked": True,
                        "lock_reason": "agreement",
                        "unchanged_count": 3,
                        "signature": "hello",
                        "last_decode_id": "private-decode",
                    }
                ]
            }
        ],
    }


def test_compatibility_public_read_strips_private_counts_but_retains_word_locks() -> (
    None
):
    transcript = Transcript(
        id=1,
        recording_id=1,
        segments=[{"id": "line", "text": "Hello!", "confidence_payload": _payload()}],
    )
    segment = serialize_transcript(
        transcript, recording_public_id="recording"
    ).segments[0]
    assert "confidence_payload" not in segment
    assert segment["locked"] is True
    assert segment["text_locked"] is True
    word = segment["words"][0]
    assert word["id"] == "word-1"
    assert word["word"] == "Hello!"
    assert word["source_end"] == 6
    assert not {"unchanged_count", "signature", "last_decode_id"}.intersection(word)


def test_unmarked_payloads_do_not_gain_settling_fields() -> None:
    payload = _payload()
    payload.pop("live_text_settling")
    assert public_settling_fields(payload) == {}
    assert public_settling_fields(None) == {}
