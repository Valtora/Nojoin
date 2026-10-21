"""The onnx-asr backstop: kept speech a window's decoder skipped is decoded again.

The fake decoder below hears a word in every run of constant, non-zero level
in the clip it is given, including a part of a word cut by the clip's edge, as
a real decoder hears a fragment. A word's level says what the word is. Levels
FRAGILE and up are words a long call skips, which is what Parakeet does to
whole sentences inside a window; a call no longer than ``long_call_s`` hears
them. Levels DEAF and up are words no call hears, so a retry skips them too.
Levels WARM and up are words a short call hears only once it has heard a word
before them, as a decoder that opens on them cold skips them. Every non-word
sample is an exact zero, as VAD writes it.
"""

import numpy as np
import pytest
import soundfile
from celery.exceptions import SoftTimeLimitExceeded

from backend.processing.engines.base import VAD_MUTED_INPUT
from backend.processing.engines.canary_engine import CanaryEngine
from backend.processing.engines.errors import TranscriptionError
from backend.processing.engines.onnx_asr_backstop import Tally, WordTiming, survey
from backend.processing.engines.parakeet_engine import ParakeetEngine

RATE = 16000
LEVEL_STEP = 0.02
FRAGILE = 8
DEAF = 2 * FRAGILE
WARM = 3 * FRAGILE
# Where the second subtoken of a word starts, after the word's first.
SUBTOKEN_S = 0.2
MUTED = {VAD_MUTED_INPUT: True}


class _Heard:
    def __init__(self, tokens, timestamps, *, timed=True):
        self.text = "".join(tokens).strip()
        self.tokens = tokens if timed else None
        self.timestamps = timestamps if timed else None


class _FakeDecoder:
    """recognize() hears each word in the clip, except fragile ones in a long call.

    ``late_s`` stamps every token that long after its sound starts, as
    Parakeet's 80 ms frames often do (before it, when negative).
    """

    def __init__(self, long_call_s, *, timed=True, late_s=0.0):
        self.long_call_s = long_call_s
        self.timed = timed
        self.late_s = late_s
        self.calls: list[float] = []

    def recognize(self, path, **_kwargs):
        samples, rate = soundfile.read(path, dtype="float32")
        duration = len(samples) / rate
        self.calls.append(duration)
        levels = np.where(
            np.abs(samples) < LEVEL_STEP,
            -1,
            np.rint((np.abs(samples) - 0.1) / LEVEL_STEP),
        ).astype(int)
        edges = np.flatnonzero(np.diff(levels)) + 1
        tokens, stamps = [], []
        for begin, end in zip(
            [0, *edges.tolist()], [*edges.tolist(), len(levels)], strict=True
        ):
            level = int(levels[begin])
            if (
                level < 0
                or DEAF <= level < WARM
                or (level >= FRAGILE and duration > self.long_call_s)
                or (level >= WARM and not tokens)
            ):
                continue
            # No decoder stamps a token before its clip starts.
            start = max(0.0, begin / rate + self.late_s)
            # Like a SentencePiece decode, the first token may lack its space.
            tokens += [" w" if tokens else "w", str(level)]
            stamps += [start, start + min(SUBTOKEN_S, (end - begin) / rate / 2)]
        return _Heard(tokens, stamps, timed=self.timed)


class _Model:
    def __init__(self, decoder):
        self.decoder = decoder

    def with_timestamps(self):
        return self.decoder


class _Layout:
    """Build VAD-muted audio from words, remembering where each one starts."""

    def __init__(self, seconds):
        self.audio = np.zeros(int(seconds * RATE), dtype="float32")
        self.words: list[tuple[float, str]] = []

    def say(self, start_s, lengths, *, fragile=False, deaf=False, warm=False):
        """Words of the given lengths back to back from start_s; returns the end."""
        at = start_s
        base = WARM if warm else DEAF if deaf else FRAGILE if fragile else 0
        for index, length in enumerate(lengths):
            level = base + index % FRAGILE
            begin, end = round(at * RATE), round((at + length) * RATE)
            self.audio[begin:end] = 0.1 + level * LEVEL_STEP
            self.words.append((round(at, 3), f"w{level}"))
            at += length
        return at

    def write(self, path):
        soundfile.write(str(path), self.audio, RATE)
        return str(path)


def _level(word):
    return int(word[1:])


def _words(result):
    return [
        (round(word["start"], 3), word["word"].strip())
        for segment in result["segments"]
        for word in segment.get("words", [])
    ]


def _transcribe(engine_class, path, config, decoder):
    engine = engine_class()
    engine._get_model = lambda _config: _Model(decoder)
    return engine.transcribe(path, config)


def _three_windows():
    """50 s cut at 15 s and 30 s, with skipped speech at three places.

    Window 2 (15 to 30 s) opens on a fragile sentence and holds another
    between two that are heard; window 3 (30 to 50 s) holds only a fragile one.
    """
    layout = _Layout(50)
    layout.say(1.0, [0.5] * 4)
    layout.say(16.0, [0.5] * 7, fragile=True)
    layout.say(20.0, [0.5] * 4)
    layout.say(23.0, [0.5] * 8, fragile=True)
    layout.say(28.0, [0.5] * 2)
    layout.say(32.0, [0.5] * 8, fragile=True)
    return layout


def test_skipped_speech_is_decoded_again_at_its_absolute_time(tmp_path):
    layout = _three_windows()
    path = layout.write(tmp_path / "m.wav")
    decoder = _FakeDecoder(long_call_s=12)

    result = _transcribe(ParakeetEngine, path, MUTED, decoder)

    # Each window, then a retry for each of its stretches: 16-19.5 and 23-27 s
    # in window 2, 32-36 s in window 3.
    assert decoder.calls == [15.0, 15.0, 3.5, 4.0, 20.0, 4.0]
    assert _words(result) == sorted(layout.words)
    # Merged, the words, their ends, the segments and the text are what one
    # call per window that skipped nothing would have given.
    assert result == _transcribe(ParakeetEngine, path, {}, _FakeDecoder(60))


def test_vad_off_input_is_decoded_as_before(tmp_path):
    layout = _three_windows()
    path = layout.write(tmp_path / "m.wav")
    for config in ({}, {VAD_MUTED_INPUT: False}):
        decoder = _FakeDecoder(long_call_s=12)

        result = _transcribe(ParakeetEngine, path, config, decoder)

        assert decoder.calls == [15.0, 15.0, 20.0]
        assert _words(result) == [
            word for word in sorted(layout.words) if _level(word[1]) < FRAGILE
        ]


def test_canary_is_never_decoded_again(tmp_path):
    # Canary returns no token timings, so a window it hears nothing in looks
    # the same as one whose words cannot be placed.
    decoder = _FakeDecoder(long_call_s=12, timed=False)

    _transcribe(
        CanaryEngine, _three_windows().write(tmp_path / "m.wav"), MUTED, decoder
    )

    assert decoder.calls == [15.0, 15.0, 20.0]


def test_a_window_whose_words_cannot_be_placed_keeps_its_text(tmp_path):
    # Without token timings a heard window has no timed words; it is not
    # decoded again. Window 3 heard nothing, so it is, and its untimed retry
    # adds nothing.
    path = _three_windows().write(tmp_path / "m.wav")
    decoder = _FakeDecoder(long_call_s=12, timed=False)

    result = _transcribe(ParakeetEngine, path, MUTED, decoder)

    assert decoder.calls == [15.0, 15.0, 20.0, 4.0]
    assert result == _transcribe(
        ParakeetEngine, path, {}, _FakeDecoder(long_call_s=12, timed=False)
    )


def test_the_live_lane_config_never_marks_its_clips_muted(tmp_path, monkeypatch):
    from backend.processing import live_transcribe

    monkeypatch.setattr(
        live_transcribe.config_manager,
        "get",
        lambda key, default=None: (
            "parakeet" if key == "transcription_backend" else default
        ),
    )
    decoder = _FakeDecoder(long_call_s=12)

    _transcribe(
        ParakeetEngine,
        _three_windows().write(tmp_path / "m.wav"),
        live_transcribe._build_live_config(),
        decoder,
    )

    assert decoder.calls == [15.0, 15.0, 20.0]


@pytest.mark.parametrize(
    ("last_word_s", "retried"),
    [(0.5, True), (0.45, False)],
    ids=["3.0s-is-decoded", "2.95s-is-left"],
)
def test_a_stretch_needs_three_seconds_of_kept_audio(tmp_path, last_word_s, retried):
    # Of the three windows (0-15, 15-30 and 30-50 s) only the second has sound.
    layout = _Layout(50)
    layout.say(16.0, [0.5] * 2)
    layout.say(18.0, [0.5] * 5 + [last_word_s], fragile=True)
    layout.say(23.0, [0.5] * 2)
    decoder = _FakeDecoder(long_call_s=12)

    result = _transcribe(
        ParakeetEngine, layout.write(tmp_path / "m.wav"), MUTED, decoder
    )

    assert decoder.calls == [15.0] + ([pytest.approx(3.0)] if retried else [])
    assert _words(result) == [
        word for word in sorted(layout.words) if retried or _level(word[1]) < FRAGILE
    ]


def test_a_word_tail_without_a_silence_is_not_decoded_twice(tmp_path):
    # The heard word at 17.5 s runs on into the skipped sentence. Its last
    # subtoken starts at 17.7 s, so the retry starts 0.8 s later, at 18.5 s:
    # inside the first skipped word, which is heard there as a fragment.
    layout = _Layout(50)
    end = layout.say(16.5, [0.5, 0.5, 0.7])
    layout.say(end, [0.5] * 8, fragile=True)
    decoder = _FakeDecoder(long_call_s=12)

    result = _transcribe(
        ParakeetEngine, layout.write(tmp_path / "m.wav"), MUTED, decoder
    )

    assert decoder.calls == [15.0, pytest.approx(22.2 - 18.5)]
    expected = sorted(layout.words)
    expected[3] = (18.5, expected[3][1])
    assert _words(result) == expected


def test_a_silence_soon_after_a_word_starts_the_stretch(tmp_path):
    # A 0.2 s silence, the shortest that counts, follows the heard word at
    # 17.0 s, and skipped speech starts at 17.6 s, before the 0.8 s guard
    # after the word's last subtoken ends: it is decoded whole.
    layout = _Layout(50)
    layout.say(16.6, [0.4, 0.4])
    layout.say(17.6, [0.5] * 8, fragile=True)
    decoder = _FakeDecoder(long_call_s=12)

    result = _transcribe(
        ParakeetEngine, layout.write(tmp_path / "m.wav"), MUTED, decoder
    )

    assert decoder.calls == [15.0, pytest.approx(4.0)]
    assert _words(result) == sorted(layout.words)


def test_a_long_stretch_is_split_at_a_silence(tmp_path):
    # The only window with sound runs 15 to 41 s. Everything after its first
    # two words is skipped: 17.5 to 41 s, with a silence at 27.5 to 28 s. A
    # call that long would skip it again, so it is decoded in two calls, cut
    # in the silence.
    layout = _Layout(60)
    layout.say(16.0, [0.5] * 2)
    layout.say(17.5, [0.5] * 20, fragile=True)
    layout.say(28.0, [0.5] * 26, fragile=True)
    path = layout.write(tmp_path / "m.wav")
    decoder = _FakeDecoder(long_call_s=15)

    result = _transcribe(ParakeetEngine, path, MUTED, decoder)

    assert decoder.calls == [26.0, 10.0, 13.5]
    assert _words(result) == sorted(layout.words)
    assert result == _transcribe(ParakeetEngine, path, {}, _FakeDecoder(60))


def test_short_input_is_one_window_and_is_backstopped_too(tmp_path):
    # 20 s fits one recognize() call; that call skips the sentence at 5-9 s.
    layout = _Layout(20)
    layout.say(1.0, [0.5] * 4)
    layout.say(5.0, [0.5] * 8, fragile=True)
    layout.say(10.0, [0.5] * 2)
    path = layout.write(tmp_path / "m.wav")
    decoder = _FakeDecoder(long_call_s=12)

    result = _transcribe(ParakeetEngine, path, MUTED, decoder)

    assert decoder.calls == [20.0, 4.0]
    assert _words(result) == sorted(layout.words)
    assert result == _transcribe(ParakeetEngine, path, {}, _FakeDecoder(60))


def test_a_silence_starting_inside_the_padding_is_measured_after_it(tmp_path):
    # The measured rule looks for a silence only from the word end padding on
    # (0.2 s after the last subtoken, 17.15 s here). The 0.22 s silence from
    # 17.3 s leaves 0.17 s past it, short of a silence, so the stretch starts
    # at the 0.8 s guard, 17.95 s, inside the first skipped word.
    layout = _Layout(50)
    layout.say(16.4, [0.6, 0.3])
    layout.say(17.52, [0.5] * 8, fragile=True)
    decoder = _FakeDecoder(long_call_s=12)

    result = _transcribe(
        ParakeetEngine, layout.write(tmp_path / "m.wav"), MUTED, decoder
    )

    assert decoder.calls == [15.0, pytest.approx(21.52 - 17.95)]
    expected = sorted(layout.words)
    expected[2] = (17.95, expected[2][1])
    assert _words(result) == expected


def _late(words, late_s=0.08):
    return [(round(start + late_s, 3), word) for start, word in sorted(words)]


@pytest.mark.parametrize("late_s", [0.08, 0.3, 0.8])
@pytest.mark.parametrize(
    "heard_first", [True, False], ids=["between-words", "before-the-first-word"]
)
def test_a_stretch_ends_at_the_silence_before_a_late_stamped_word(
    tmp_path, late_s, heard_first
):
    # The heard word whose sound starts at 9.92 s is stamped up to 0.8 s
    # later. The stretch before it ends where the silence before that word
    # starts, at 7 s, so the word's onset is not decoded again as a second
    # copy, whether or not a heard word precedes the stretch.
    layout = _Layout(20)
    if heard_first:
        layout.say(1.0, [0.5])
    layout.say(3.0, [0.5] * 8, fragile=True)
    layout.say(9.92, [1.0])
    decoder = _FakeDecoder(long_call_s=12, late_s=late_s)

    result = _transcribe(
        ParakeetEngine, layout.write(tmp_path / "m.wav"), MUTED, decoder
    )

    assert decoder.calls == [20.0, pytest.approx(4.0)]
    assert _words(result) == _late(layout.words, late_s)


def test_a_word_stamped_inside_the_silence_before_it_ends_the_stretch_there(
    tmp_path,
):
    # The heard word at 7.22 s is stamped 0.1 s early, inside the 0.22 s
    # silence before it. That silence still counts, so the stretch ends where
    # it starts, at 7 s, rather than 0.16 s before the stamp.
    layout = _Layout(20)
    layout.say(1.0, [0.5])
    layout.say(3.0, [0.5] * 8, fragile=True)
    layout.say(7.22, [0.5])
    decoder = _FakeDecoder(long_call_s=12, late_s=-0.1)

    result = _transcribe(
        ParakeetEngine, layout.write(tmp_path / "m.wav"), MUTED, decoder
    )

    assert decoder.calls == [20.0, pytest.approx(4.0)]
    assert len(_words(result)) == len(layout.words)


def test_a_silence_starting_on_a_word_stamp_is_not_before_it():
    # Kept audio runs from 3 s to the word stamped at 10 s, and a silence
    # starts right there. It is not a silence before the word, so the hole
    # stops 0.16 s short of the stamp.
    audio = np.zeros(20 * RATE, dtype="float32")
    audio[3 * RATE : 10 * RATE] = 0.1
    audio[round(10.5 * RATE) : 11 * RATE] = 0.1
    words = [WordTiming(10.0, 10.0)]

    assert survey(audio, RATE, words).holes == [(3 * RATE, round(9.84 * RATE))]


def test_a_stretch_without_a_silence_before_the_next_word_stops_short_of_it(
    tmp_path,
):
    # The skipped sentence runs straight into the heard word at 7.0 s,
    # stamped at 7.08 s. With no silence to end on, the stretch stops 0.16 s
    # before that stamp, at 6.92 s: inside the last skipped word, which is
    # heard there as a fragment, and short of the heard word.
    layout = _Layout(20)
    layout.say(1.0, [0.5])
    end = layout.say(3.0, [0.5] * 8, fragile=True)
    layout.say(end, [0.5])
    decoder = _FakeDecoder(long_call_s=12, late_s=0.08)

    result = _transcribe(
        ParakeetEngine, layout.write(tmp_path / "m.wav"), MUTED, decoder
    )

    assert decoder.calls == [20.0, pytest.approx(3.92)]
    assert _words(result) == _late(layout.words)


def test_input_just_over_a_window_is_cut_at_a_pause(tmp_path):
    # 30.1 s leaves too little for two windows of 15 s or more. The cut used
    # to fall at 30 s, inside the word from 29.5 to 30.1 s, which was then
    # heard in both windows; it falls in the pause at 12 s instead.
    layout = _Layout(30.1)
    layout.say(0.0, [0.5] * 24)
    layout.say(12.5, [0.5] * 34 + [0.6])
    decoder = _FakeDecoder(long_call_s=60)

    result = _transcribe(ParakeetEngine, layout.write(tmp_path / "m.wav"), {}, decoder)

    assert decoder.calls == [12.0, pytest.approx(18.1)]
    assert _words(result) == sorted(layout.words)


def test_a_stretch_just_over_the_retry_length_is_cut_at_a_pause(tmp_path):
    # The stretch from 2.5 to 22.6 s is 20.1 s, too little for two retries of
    # 10 s or more. The cut used to fall 20 s in, at 22.5 s, inside the word
    # from 22.0 to 22.6 s; it falls in the pause at 8 s instead.
    layout = _Layout(30)
    layout.say(1.0, [0.5])
    layout.say(2.5, [0.5] * 11, fragile=True)
    layout.say(8.5, [0.5] * 27 + [0.6], fragile=True)
    decoder = _FakeDecoder(long_call_s=20)

    result = _transcribe(
        ParakeetEngine, layout.write(tmp_path / "m.wav"), MUTED, decoder
    )

    assert decoder.calls == [30.0, pytest.approx(5.5), pytest.approx(14.6)]
    assert _words(result) == sorted(layout.words)


class _FailingDecoder(_FakeDecoder):
    """A _FakeDecoder whose ``fail_at``-th recognize() call raises ``error``."""

    def __init__(self, error, fail_at, **kwargs):
        super().__init__(**kwargs)
        self.error = error
        self.fail_at = fail_at

    def recognize(self, path, **kwargs):
        heard = super().recognize(path, **kwargs)
        if len(self.calls) == self.fail_at:
            raise self.error
        return heard


def test_a_failed_retry_keeps_the_window_and_the_other_retries(tmp_path):
    # The third call, the retry of 16-19.5 s, fails. The window keeps the
    # words its own call heard, and its other stretch and window 3's are
    # still decoded again.
    layout = _three_windows()
    decoder = _FailingDecoder(RuntimeError("decoder broke"), 3, long_call_s=12)

    result = _transcribe(
        ParakeetEngine, layout.write(tmp_path / "m.wav"), MUTED, decoder
    )

    assert decoder.calls == [15.0, 15.0, 3.5, 4.0, 20.0, 4.0]
    assert _words(result) == [
        word for word in sorted(layout.words) if not 16.0 <= word[0] < 19.5
    ]


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (RuntimeError("CUDA failure 2: out of memory"), TranscriptionError),
        (SoftTimeLimitExceeded(), SoftTimeLimitExceeded),
    ],
    ids=["out-of-memory", "time-limit"],
)
def test_a_retry_out_of_memory_or_interrupted_still_stops_the_run(
    tmp_path, error, raised
):
    decoder = _FailingDecoder(error, 3, long_call_s=12)

    with pytest.raises(raised):
        _transcribe(
            ParakeetEngine, _three_windows().write(tmp_path / "m.wav"), MUTED, decoder
        )

    assert decoder.calls == [15.0, 15.0, 3.5]


def _tallies(caplog):
    return [r for r in caplog.records if hasattr(r, "backstop_tally")]


def test_the_backstop_counts_what_it_leaves_once_per_recording(tmp_path, caplog):
    # The retry of 3-12.5 s hears the sentence at 3-7 s but, like the
    # window's own call, not the 4 s of speech at 8-12.5 s. Its second try,
    # from the last word's last subtoken at 6.7 s, hears only that word's
    # tail, so the 4 s are still wordless. The 1 s at 15.5 s is too short to
    # retry.
    layout = _Layout(20)
    layout.say(1.0, [0.5] * 2)
    layout.say(3.0, [0.5] * 8, fragile=True)
    layout.say(8.0, [0.5] * 3, deaf=True)
    layout.say(10.0, [0.5] * 5, deaf=True)
    layout.say(13.5, [0.5] * 2)
    layout.say(15.5, [0.5] * 2, deaf=True)
    layout.say(17.5, [0.5])
    decoder = _FakeDecoder(long_call_s=12)

    with caplog.at_level("INFO"):
        _transcribe(ParakeetEngine, layout.write(tmp_path / "m.wav"), MUTED, decoder)

    assert decoder.calls == [20.0, 9.5, pytest.approx(5.8)]
    [record] = _tallies(caplog)
    assert record.getMessage() == (
        "parakeet: backstop: 1 holes retried (9.5 s, 1 calls); 1 still wordless "
        "retried again from the word before them (4.0 s kept, 1 calls), finding "
        "0 words in 0 (0.0 s kept no longer wordless); 0 still wordless with no "
        "word before them not retried again (0.0 s kept); 1 holes (4.0 s kept) "
        "still wordless; 1 holes under 3 s skipped (1.0 s kept)."
    )
    assert record.backstop_tally == Tally(
        retried=1,
        retried_frames=9.5 * RATE,
        calls=1,
        retried_again=1,
        retried_again_kept_frames=4 * RATE,
        calls_again=1,
        wordless=1,
        wordless_kept_frames=4 * RATE,
        short=1,
        short_kept_frames=RATE,
    )


def _transcribe_counted(path, decoder, caplog):
    with caplog.at_level("INFO"):
        result = _transcribe(ParakeetEngine, path, MUTED, decoder)
    [record] = _tallies(caplog)
    return result, record.backstop_tally


def test_a_hole_still_wordless_is_tried_again_from_the_word_before(tmp_path, caplog):
    # The sentence at 3-7 s is heard only after another word. The window's
    # call is too long for it, and its retry opens on it cold. The second try
    # starts at the last subtoken of the word before, 1.7 s: it hears that
    # word's tail there, which is dropped, and then the sentence.
    layout = _Layout(20)
    layout.say(1.0, [0.5] * 2)
    layout.say(3.0, [0.5] * 8, warm=True)
    layout.say(10.0, [0.5] * 2)
    decoder = _FakeDecoder(long_call_s=12)

    result, tally = _transcribe_counted(
        layout.write(tmp_path / "m.wav"), decoder, caplog
    )

    assert decoder.calls == [20.0, 4.0, pytest.approx(7.0 - 1.7)]
    assert _words(result) == sorted(layout.words)
    assert (tally.retried_again, tally.calls_again, tally.wordless) == (1, 1, 0)
    assert (tally.recovered, tally.recovered_words) == (1, 8)
    assert tally.recovered_kept_frames == 4 * RATE


def test_a_hole_with_no_word_before_it_gets_no_second_try(tmp_path, caplog):
    # The same sentence opens the window: a word follows it, none precedes it.
    layout = _Layout(20)
    layout.say(3.0, [0.5] * 8, warm=True)
    layout.say(10.0, [0.5] * 2)
    decoder = _FakeDecoder(long_call_s=12)

    result, tally = _transcribe_counted(
        layout.write(tmp_path / "m.wav"), decoder, caplog
    )

    assert decoder.calls == [20.0, 4.0]
    assert _words(result) == [w for w in layout.words if _level(w[1]) < FRAGILE]
    assert (tally.retried_again, tally.calls_again) == (0, 0)
    assert (tally.no_previous_word, tally.no_previous_word_kept_frames) == (
        1,
        4 * RATE,
    )
    assert (tally.wordless, tally.wordless_kept_frames) == (1, 4 * RATE)


def test_a_hole_is_never_decoded_a_third_time(tmp_path, caplog):
    # The second try, from 1.7 s, hears the sentence at 3-7 s but not the
    # 4.5 s at 8-12.5 s. That is a hole after a word now, and stays wordless.
    layout = _Layout(20)
    layout.say(1.0, [0.5] * 2)
    layout.say(3.0, [0.5] * 8, warm=True)
    layout.say(8.0, [0.5] * 9, deaf=True)
    layout.say(14.0, [0.5] * 2)
    decoder = _FakeDecoder(long_call_s=12)

    result, tally = _transcribe_counted(
        layout.write(tmp_path / "m.wav"), decoder, caplog
    )

    assert decoder.calls == [20.0, 9.5, pytest.approx(12.5 - 1.7)]
    assert _words(result) == [
        w for w in sorted(layout.words) if not DEAF <= _level(w[1]) < WARM
    ]
    assert (tally.retried_again, tally.recovered, tally.recovered_words) == (1, 1, 8)
    assert tally.recovered_kept_frames == 4 * RATE
    assert (tally.wordless, tally.wordless_kept_frames) == (1, 4.5 * RATE)


def test_a_failed_second_try_keeps_what_the_first_retry_found(tmp_path, caplog):
    # The third call, the second try at 1.7-12.5 s, fails; the sentence it
    # would have found stays missing, and the hole is counted as wordless.
    layout = _Layout(20)
    layout.say(1.0, [0.5] * 2)
    layout.say(3.0, [0.5] * 8, warm=True)
    layout.say(8.0, [0.5] * 9, deaf=True)
    layout.say(14.0, [0.5] * 2)
    decoder = _FailingDecoder(RuntimeError("decoder broke"), 3, long_call_s=12)

    result, tally = _transcribe_counted(
        layout.write(tmp_path / "m.wav"), decoder, caplog
    )

    assert decoder.calls == [20.0, 9.5, pytest.approx(10.8)]
    assert _words(result) == [w for w in layout.words if _level(w[1]) < FRAGILE]
    assert (tally.retried_again, tally.calls_again, tally.recovered) == (1, 1, 0)
    assert (tally.wordless, tally.wordless_kept_frames) == (1, 8.5 * RATE)


def test_the_word_before_decoded_again_does_not_move_what_is_left(tmp_path, caplog):
    # Unheard speech runs on from the heard word at 1.5-2.2 s with no
    # silence, so the hole starts 0.8 s after its last subtoken, at 2.5 s.
    # The second try, from 1.7 s, hears that word's tail again, with its own
    # last subtoken at 1.9 s; dropped, it does not shorten the 4.5 s left.
    layout = _Layout(20)
    end = layout.say(1.0, [0.5, 0.7])
    layout.say(end, [0.6] * 8, deaf=True)
    layout.say(10.0, [0.5] * 2)
    decoder = _FakeDecoder(long_call_s=12)

    result, tally = _transcribe_counted(
        layout.write(tmp_path / "m.wav"), decoder, caplog
    )

    assert decoder.calls == [20.0, 4.5, pytest.approx(7.0 - 1.7)]
    assert _words(result) == [w for w in layout.words if _level(w[1]) < FRAGILE]
    assert (tally.wordless, tally.wordless_kept_frames) == (1, 4.5 * RATE)


def test_the_counts_cover_every_window_in_one_line(tmp_path, caplog):
    with caplog.at_level("INFO"):
        _transcribe(
            ParakeetEngine,
            _three_windows().write(tmp_path / "m.wav"),
            MUTED,
            _FakeDecoder(long_call_s=12),
        )

    [record] = _tallies(caplog)
    assert (record.backstop_tally.retried, record.backstop_tally.calls) == (3, 3)
    assert record.backstop_tally.wordless == 0


def test_nothing_is_counted_without_the_backstop(tmp_path, caplog):
    with caplog.at_level("INFO"):
        _transcribe(
            ParakeetEngine,
            _three_windows().write(tmp_path / "m.wav"),
            {},
            _FakeDecoder(long_call_s=12),
        )

    assert _tallies(caplog) == []


def test_a_failed_piece_of_a_split_stretch_keeps_the_other_piece(tmp_path):
    # The stretch from 17.5 to 41 s is decoded in two calls; the first fails,
    # and the second still adds its words. That leaves 17.5-27.5 s wordless,
    # and its second try, from the word before at 16.7 s, hears it.
    layout = _Layout(60)
    layout.say(16.0, [0.5] * 2)
    layout.say(17.5, [0.5] * 20, fragile=True)
    layout.say(28.0, [0.5] * 26, fragile=True)
    decoder = _FailingDecoder(RuntimeError("decoder broke"), 2, long_call_s=15)

    result = _transcribe(
        ParakeetEngine, layout.write(tmp_path / "m.wav"), MUTED, decoder
    )

    assert decoder.calls == [26.0, 10.0, 13.5, pytest.approx(27.5 - 16.7)]
    assert _words(result) == sorted(layout.words)
