"""Words an ASR engine placed in digital silence are dropped before the merge (#508).

The audio here is what the VAD stage writes: speech kept as it was and every
other sample an exact zero. A "word" in it is a stretch of constant, non-zero
level.
"""

import logging
import random

import numpy as np
import pytest
import soundfile

from backend.processing import unheard_words
from backend.processing.unheard_words import (
    UNHEARD_WORD_GUARD_S,
    UnheardWords,
    digital_silence_runs,
    remove_unheard_words,
)
from backend.worker.tasks.final_segments import drop_unheard_words

RATE = 16000
GUARD = UNHEARD_WORD_GUARD_S


def _write(path, spans_s, duration_s, *, channels=1):
    """A muted WAV: non-zero in each (start, end) of ``spans_s``, zero elsewhere."""
    audio = np.zeros((round(duration_s * RATE), channels), dtype=np.float32)
    for start, end in spans_s:
        audio[round(start * RATE) : round(end * RATE)] = 0.25
    soundfile.write(str(path), audio, RATE, subtype="PCM_16")
    return str(path)


def _word(start, end, text):
    return {"start": start, "end": end, "word": text}


def _segment(*words):
    return {
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "text": "".join(word["word"] for word in words),
        "words": list(words),
    }


# --- reading the runs ------------------------------------------------------


def _reference_runs(silent, min_frames):
    runs, begin = [], None
    for index, quiet in enumerate([*silent, False]):
        if quiet and begin is None:
            begin = index
        elif not quiet and begin is not None:
            if index - begin >= min_frames:
                runs.append((begin, index))
            begin = None
    return runs


@pytest.mark.parametrize("seed", range(20))
def test_zero_runs_match_a_frame_by_frame_reading_across_blocks(seed):
    rng = random.Random(seed)
    silent = []
    while len(silent) < 400:
        silent.extend([rng.random() < 0.5] * rng.randint(1, 40))
    frames = np.where(np.array(silent)[:, None], 0.0, 0.5)
    size = rng.randint(1, 60)
    blocks = [frames[i : i + size] for i in range(0, len(frames), size)]
    min_frames = rng.randint(1, 12)

    assert unheard_words._zero_runs(blocks, min_frames) == _reference_runs(
        silent, min_frames
    )


def test_runs_are_read_in_seconds_across_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(unheard_words, "_BLOCK_FRAMES", 1000)
    path = _write(tmp_path / "muted.wav", [(1.0, 2.0), (2.3, 4.0)], 6.0)

    assert digital_silence_runs(path, min_run_s=0.5) == [(0.0, 1.0), (4.0, 6.0)]
    assert digital_silence_runs(path, min_run_s=0.2) == [
        (0.0, 1.0),
        (2.0, 2.3),
        (4.0, 6.0),
    ]


def test_a_frame_is_silent_only_when_every_channel_is_zero(tmp_path):
    audio = np.zeros((2 * RATE, 2), dtype=np.float32)
    audio[RATE:, 1] = 0.25
    path = tmp_path / "stereo.wav"
    soundfile.write(str(path), audio, RATE, subtype="PCM_16")

    assert digital_silence_runs(str(path), min_run_s=0.5) == [(0.0, 1.0)]


# --- dropping the words ----------------------------------------------------

RUNS = [(10.0, 20.0)]


def test_a_word_well_inside_a_silence_goes_with_its_segment():
    hallucination = _segment(_word(14.0, 14.4, " Thank"), _word(14.4, 14.8, " you."))
    speech = _segment(_word(20.1, 20.5, " Right,"), _word(20.5, 20.9, " so"))
    result = {"text": " Thank you. Right, so", "segments": [hallucination, speech]}

    filtered, dropped = remove_unheard_words(result, RUNS)

    assert dropped == UnheardWords(words=2, segments=1)
    assert filtered["segments"] == [speech]
    assert filtered["text"] == "Right, so"


@pytest.mark.parametrize(
    ("start", "end", "kept"),
    [
        (10.0 + GUARD, 20.0 - GUARD, False),  # exactly the guard each side
        (10.0 + GUARD - 0.01, 12.0, True),  # nearer the run's start than the guard
        (18.0, 20.0 - GUARD + 0.01, True),  # nearer the run's end than the guard
        (9.9, 10.8, True),  # straddles the run's start
        (19.4, 20.2, True),  # straddles the run's end
        (15.0, 15.0, False),  # a zero-length word deep in silence
        (25.0, 25.4, True),  # outside every run
    ],
)
def test_only_a_word_with_the_guard_to_spare_on_both_sides_is_dropped(start, end, kept):
    word = _word(start, end, " hm")
    filtered, dropped = remove_unheard_words({"segments": [_segment(word)]}, RUNS)

    assert (filtered["segments"] == [_segment(word)]) is kept
    assert dropped.words == (0 if kept else 1)


@pytest.mark.parametrize("offset_s", [0.0, 7000.0])
@pytest.mark.parametrize(("excess_s", "dropped_words"), [(1e-9, 1), (1e-3, 0)])
def test_float_error_does_not_decide_the_guard(offset_s, excess_s, dropped_words):
    run = (offset_s + 10.0, offset_s + 20.0)
    word = _word(run[0] + GUARD - excess_s, run[1] - GUARD + excess_s, " hm")

    _, dropped = remove_unheard_words({"segments": [_segment(word)]}, [run])

    assert dropped.words == dropped_words


def test_a_segment_losing_some_words_keeps_its_text_and_edges_to_match():
    first = _word(8.0, 8.4, " We")
    second = _word(8.4, 9.0, " agreed.")
    invented = _word(15.0, 15.5, " Thanks.")
    segment = {"start": 7.9, "end": 16.0, "text": " We agreed. Thanks."}
    segment["words"] = [first, second, invented]
    later = {"start": 30.0, "end": 31.0, "text": " Next.", "words": []}
    result = {"text": " We agreed. Thanks. Next.", "segments": [segment, later]}

    filtered, dropped = remove_unheard_words(result, RUNS)

    assert dropped == UnheardWords(words=1, segments=0)
    trimmed = filtered["segments"][0]
    assert trimmed["words"] == [first, second]
    assert trimmed["text"] == "We agreed."
    # Only the edge whose word went moves.
    assert (trimmed["start"], trimmed["end"]) == (7.9, 9.0)
    assert filtered["segments"][1] is later
    assert filtered["text"] == "We agreed. Next."


def test_a_segment_without_word_times_is_judged_on_its_own_span():
    inside = {"start": 12.0, "end": 13.0, "text": " Thank you."}
    across = {"start": 19.0, "end": 24.0, "text": " Shall we start?"}
    result = {"text": "", "segments": [inside, across]}

    filtered, dropped = remove_unheard_words(result, RUNS)

    assert dropped == UnheardWords(words=0, segments=1)
    assert filtered["segments"] == [across]


def test_nothing_in_silence_leaves_the_result_as_it_was():
    words = [_word(1.0, 1.4, " Hello"), _word(9.7, 10.4, " there")]
    result = {"text": " Hello there", "segments": [_segment(*words)], "language": "en"}
    snapshot = {**result, "segments": [dict(seg) for seg in result["segments"]]}

    filtered, dropped = remove_unheard_words(result, RUNS)

    assert dropped == UnheardWords(words=0, segments=0)
    assert filtered == snapshot
    assert result == snapshot


def test_the_engines_result_is_not_changed():
    hallucination = _segment(_word(14.0, 14.8, " Thank you."))
    result = {"text": " Thank you.", "segments": [hallucination]}

    filtered, _ = remove_unheard_words(result, RUNS)

    assert filtered["segments"] == []
    assert result == {"text": " Thank you.", "segments": [hallucination]}


def test_a_word_without_usable_times_is_kept():
    word = {"start": None, "end": 15.0, "word": " hm"}
    filtered, dropped = remove_unheard_words({"segments": [_segment(word)]}, RUNS)

    assert dropped.words == 0
    assert filtered["segments"][0]["words"] == [word]


# --- the pipeline's stage --------------------------------------------------


def test_the_stage_drops_what_the_engine_placed_in_the_muted_audio(tmp_path, caplog):
    path = _write(tmp_path / "rec_vad_processed.wav", [(1.0, 3.0), (20.0, 22.0)], 24)
    speech = _segment(_word(1.2, 1.6, " Morning"), _word(1.6, 2.4, " all."))
    hallucination = _segment(_word(11.0, 11.4, " Thank"), _word(11.4, 11.8, " you."))
    result = {"text": "", "segments": [speech, hallucination], "language": "en"}

    with caplog.at_level(logging.INFO):
        filtered = drop_unheard_words(result, path, 42)

    assert filtered["segments"] == [speech]
    assert filtered["language"] == "en"
    assert '"words_dropped":2' in caplog.text
    assert '"segments_dropped":1' in caplog.text


def test_unmuted_audio_keeps_every_word(tmp_path):
    path = _write(tmp_path / "rec_vad.wav", [(0.0, 24.0)], 24)
    result = {"segments": [_segment(_word(11.0, 11.8, " Thank you."))]}

    assert drop_unheard_words(result, path, 42) == result


def test_audio_that_cannot_be_read_keeps_every_word(tmp_path, caplog):
    result = {"segments": [_segment(_word(11.0, 11.8, " Thank you."))]}

    with caplog.at_level(logging.WARNING):
        filtered = drop_unheard_words(result, str(tmp_path / "missing.wav"), 42)

    assert filtered is result
    assert "Kept every ASR word" in caplog.text
