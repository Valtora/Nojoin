"""Final split choices retain real lexical boundaries and the existing public shape."""

from copy import deepcopy
from uuid import UUID

from backend.models.live_settling_public import public_settling_fields
from backend.utils.final_word_boundaries import final_words


def test_final_boundaries_exclude_contiguous_forced_fragments_and_native_contractions() -> (
    None
):
    words = [
        {"word": "Don", "source_id": "s", "source_start": 0, "source_end": 3},
        {"word": "'", "source_id": "s", "source_start": 3, "source_end": 4},
        {"word": "t", "source_id": "s", "source_start": 4, "source_end": 5},
        {"word": " split", "source_id": "s", "source_start": 5, "source_end": 11},
        {"word": "n't"},
    ]
    payload = {
        "asr_segments": [
            {
                "words": [
                    {**word, "start_ms": i * 100, "end_ms": (i + 1) * 100}
                    for i, word in enumerate(words)
                ]
            }
        ]
    }
    original = deepcopy(payload)
    public = public_settling_fields(payload, final_utterance_id="line-1")
    assert set(public) == {"words"}
    assert [word.get("join_left") for word in public["words"]] == [
        None,
        True,
        True,
        None,
        True,
    ]
    assert [word["order"] for word in public["words"]] == list(range(5))
    assert all(str(UUID(word["id"])) == word["id"] for word in public["words"])
    assert all(
        word["locked"] and word["lock_reason"] is None for word in public["words"]
    )
    assert all("locked" not in word for word in payload["asr_segments"][0]["words"])
    assert payload == original
    assert final_words(payload, "line-1") == final_words(deepcopy(payload), "line-1")
    assert (
        final_words(payload, "line-1")[0]["id"]
        != final_words(payload, "line-2")[0]["id"]
    )


def test_wordless_final_output_has_no_split_boundaries() -> None:
    assert (
        public_settling_fields(
            {"asr_segments": [{"words": []}]}, final_utterance_id="line-1"
        )
        == {}
    )


def test_stale_final_evidence_cannot_expose_boundaries_for_different_wording() -> None:
    payload = {
        "asr_segments": [
            {
                "words": [
                    {"word": "Previous", "start_ms": 0, "end_ms": 100},
                    {"word": "wording", "start_ms": 100, "end_ms": 200},
                ]
            }
        ]
    }
    assert (
        public_settling_fields(
            payload, final_utterance_id="line-1", final_text="Current wording"
        )
        == {}
    )
    assert final_words(payload, "line-1", text="Current wording") == []
