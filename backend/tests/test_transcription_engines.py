"""Tests for the pluggable transcription engine (config + dispatch + engine schema)."""

import pytest

from backend.processing import transcribe

# validate_config_value is a method on the ConfigManager singleton, not a
# module-level function, so it is accessed via the exported config_manager instance.
from backend.utils.config_manager import DEFAULT_SYSTEM_CONFIG, config_manager

validate_config_value = config_manager.validate_config_value


def test_config_default_transcription_backend():
    """The default config selects the whisper backend and the v3 parakeet model."""
    assert DEFAULT_SYSTEM_CONFIG["transcription_backend"] == "whisper"
    assert DEFAULT_SYSTEM_CONFIG["parakeet_model"] == "parakeet-tdt-0.6b-v3"


def test_validate_config_value_transcription_backend():
    """transcription_backend accepts known backends and rejects unknown values."""
    assert validate_config_value("transcription_backend", "whisper") is True
    assert validate_config_value("transcription_backend", "parakeet") is True
    assert validate_config_value("transcription_backend", "bogus") is False


def test_validate_config_value_canary_backend():
    """The canary backend is a valid transcription_backend; the default is unchanged."""
    assert validate_config_value("transcription_backend", "canary") is True
    assert DEFAULT_SYSTEM_CONFIG["transcription_backend"] == "whisper"
    assert DEFAULT_SYSTEM_CONFIG["canary_model"] == "nemo-canary-1b-v2"


@pytest.fixture(autouse=True)
def _clean_engine_registry():
    """Keep the dispatcher engine registry isolated between tests."""
    transcribe._ENGINE_REGISTRY.clear()
    yield
    transcribe._ENGINE_REGISTRY.clear()


class _FakeEngine:
    """Minimal stand-in for a TranscriptionEngine used by dispatcher tests."""

    def __init__(self, name="whisper"):
        self.name = name
        self.transcribe_called_with = None
        self.released = False

    def transcribe(self, audio_path, config):
        self.transcribe_called_with = (audio_path, config)
        return {"text": "fake", "language": "en", "segments": []}

    def release(self):
        self.released = True


def test_dispatcher_selects_whisper():
    """transcribe_audio routes to the whisper engine in the registry."""
    fake = _FakeEngine("whisper")
    transcribe._ENGINE_REGISTRY["whisper"] = fake
    result = transcribe.transcribe_audio(
        "meeting.wav", {"transcription_backend": "whisper"}
    )
    assert fake.transcribe_called_with is not None
    assert fake.transcribe_called_with[0] == "meeting.wav"
    assert result == {"text": "fake", "language": "en", "segments": []}


def test_dispatcher_unknown_backend_returns_none():
    """An unknown backend yields None instead of raising."""
    result = transcribe.transcribe_audio("x.wav", {"transcription_backend": "bogus"})
    assert result is None


def test_whisper_engine_emits_canonical_schema(monkeypatch):
    """WhisperEngine.transcribe returns the canonical dict schema."""
    from backend.processing.engines import whisper_engine
    from backend.processing.engines.whisper_engine import WhisperEngine

    class _FakeModel:
        def transcribe(self, audio_path, **kwargs):
            return {
                "text": "hi",
                "language": "en",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "text": " hi",
                        "words": [{"start": 0.0, "end": 1.0, "word": " hi"}],
                    }
                ],
            }

    monkeypatch.setattr(
        whisper_engine.whisper, "load_model", lambda *a, **k: _FakeModel()
    )
    monkeypatch.setattr(
        whisper_engine, "ensure_ffmpeg_in_path", lambda: None, raising=False
    )
    monkeypatch.setattr(whisper_engine.os.path, "exists", lambda p: True)
    # Avoid leaking a fake model into the shared module-level cache.
    monkeypatch.setattr(whisper_engine, "_model_cache", {})

    engine = WhisperEngine()
    result = engine.transcribe("meeting.wav", {})

    assert isinstance(result["text"], str)
    assert isinstance(result["segments"], list)
    for segment in result["segments"]:
        assert "start" in segment
        assert "end" in segment
        assert "text" in segment


def test_whisper_engine_passes_forced_language(monkeypatch):
    from backend.processing.engines import whisper_engine
    from backend.processing.engines.whisper_engine import WhisperEngine

    captured = {}

    class _FakeModel:
        def transcribe(self, audio_path, **kwargs):
            captured.update(kwargs)
            return {"text": "bonjour", "language": "fr", "segments": []}

    monkeypatch.setattr(
        whisper_engine.whisper, "load_model", lambda *a, **k: _FakeModel()
    )
    monkeypatch.setattr(whisper_engine.os.path, "exists", lambda p: True)
    monkeypatch.setattr(whisper_engine, "_model_cache", {})

    result = WhisperEngine().transcribe(
        "meeting.wav",
        {"transcription_language": "fr"},
    )

    assert result["language"] == "fr"
    assert captured["language"] == "fr"


def test_release_model_cache_calls_engines():
    """release_model_cache delegates to every instantiated engine."""
    fake = _FakeEngine("whisper")
    transcribe._ENGINE_REGISTRY["whisper"] = fake
    transcribe.release_model_cache()
    assert fake.released is True


# --- Parakeet mapper tests (pure function, no onnx / no mocks needed) ---

from backend.processing.engines.parakeet_engine import map_parakeet_result


def test_parakeet_mapping_basic():
    """Two close words map to one segment with correct word timings."""
    result = map_parakeet_result(
        text="hello world",
        tokens=[" hello", " world"],
        timestamps=[0.0, 0.5],
        audio_duration=1.0,
    )
    assert len(result["segments"]) == 1
    segment = result["segments"][0]
    assert len(segment["words"]) == 2
    assert "hello" in segment["text"]
    assert "world" in segment["text"]
    assert segment["words"][0]["start"] == 0.0
    assert segment["words"][0]["end"] == 0.5
    assert segment["words"][1]["start"] == 0.5
    assert segment["words"][1]["end"] == 1.0
    for word in segment["words"]:
        assert word["word"].startswith(" ")


def test_parakeet_mapping_subword_tokens():
    """Subword tokens are joined into whole words at space-prefixed boundaries."""
    result = map_parakeet_result(
        text="unbelievable good",
        tokens=[" un", "believ", "able", " good"],
        timestamps=[0.0, 0.1, 0.2, 0.4],
    )
    words = result["segments"][0]["words"]
    assert len(words) == 2
    assert words[0]["word"] == " unbelievable"
    assert words[1]["word"] == " good"


def test_parakeet_mapping_segment_split_on_pause():
    """A gap larger than the pause threshold splits words into two segments."""
    result = map_parakeet_result(
        text="hello world",
        tokens=[" hello", " world"],
        timestamps=[0.0, 2.0],
        audio_duration=3.0,
    )
    assert len(result["segments"]) == 2


def test_parakeet_mapping_word_leading_space():
    """Every word in every segment carries a leading space."""
    result = map_parakeet_result(
        text="one two three four",
        tokens=[" one", " two", " three", " four"],
        timestamps=[0.0, 1.5, 3.0, 4.5],
        audio_duration=5.0,
    )
    for segment in result["segments"]:
        for word in segment["words"]:
            assert word["word"].startswith(" ")


def test_parakeet_mapping_fallback_no_timestamps():
    """Missing timestamps yield a single fallback segment with no words."""
    result = map_parakeet_result(text="some text", tokens=None, timestamps=None)
    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == "some text"
    assert "words" not in result["segments"][0] or not result["segments"][0]["words"]


def test_dispatcher_selects_parakeet():
    """transcribe_audio routes to the parakeet engine in the registry."""
    fake = _FakeEngine("parakeet")
    transcribe._ENGINE_REGISTRY["parakeet"] = fake
    result = transcribe.transcribe_audio(
        "meeting.wav", {"transcription_backend": "parakeet"}
    )
    assert fake.transcribe_called_with is not None
    assert fake.transcribe_called_with[0] == "meeting.wav"
    assert result == {"text": "fake", "language": "en", "segments": []}


def test_dispatcher_selects_canary():
    """transcribe_audio routes to the canary engine in the registry."""
    fake = _FakeEngine("canary")
    transcribe._ENGINE_REGISTRY["canary"] = fake
    result = transcribe.transcribe_audio(
        "meeting.wav", {"transcription_backend": "canary"}
    )
    assert fake.transcribe_called_with is not None
    assert fake.transcribe_called_with[0] == "meeting.wav"
    assert result == {"text": "fake", "language": "en", "segments": []}


# --- onnx-asr long-audio chunking (shared OnnxAsrEngine base) ---


class _FakeRecognized:
    """A fixed onnx-asr recognize() result with one timestamped token."""

    text = "chunk"
    tokens = [" chunk"]
    timestamps = [0.0]


def _fake_onnx_model(calls):
    """Build a fake onnx-asr model whose recognize() records each call's path."""

    class _Recognizer:
        def recognize(self, path):
            calls.append(path)
            return _FakeRecognized()

    class _Model:
        def with_timestamps(self):
            return _Recognizer()

    return _Model()


def _write_silence(path, seconds, sample_rate=16000):
    """Write `seconds` of 16kHz mono silence to `path`."""
    import numpy as np
    import soundfile

    soundfile.write(
        str(path), np.zeros(int(seconds * sample_rate), dtype="float32"), sample_rate
    )


def test_onnx_asr_short_audio_single_pass(tmp_path, monkeypatch):
    """Audio within the single-pass limit goes through one recognize() call."""
    from backend.processing.engines.parakeet_engine import ParakeetEngine

    audio_path = tmp_path / "short.wav"
    _write_silence(audio_path, 5)

    calls = []
    engine = ParakeetEngine()
    monkeypatch.setattr(engine, "_get_model", lambda config: _fake_onnx_model(calls))

    result = engine.transcribe(str(audio_path), {})

    assert calls == [str(audio_path)]
    assert result["text"] == "chunk"
    assert len(result["segments"]) == 1


def test_onnx_asr_chunks_long_audio(tmp_path, monkeypatch):
    """Audio beyond the limit is split into windows and merged into absolute time."""
    from backend.processing.engines import onnx_asr_engine
    from backend.processing.engines.parakeet_engine import ParakeetEngine

    # Shrink the window so the fixture audio stays tiny: 4s windows, 10s audio.
    monkeypatch.setattr(onnx_asr_engine, "MAX_CHUNK_DURATION_S", 4.0)
    monkeypatch.setattr(onnx_asr_engine, "CHUNK_SNAP_RADIUS_S", 0.5)

    audio_path = tmp_path / "long.wav"
    _write_silence(audio_path, 10)

    calls = []
    engine = ParakeetEngine()
    monkeypatch.setattr(engine, "_get_model", lambda config: _fake_onnx_model(calls))

    result = engine.transcribe(str(audio_path), {})

    # recognize() ran once per window, on temp files — never on the source path.
    assert len(calls) == 3
    assert str(audio_path) not in calls
    # Per-window transcripts and segments are concatenated.
    assert result["text"] == "chunk chunk chunk"
    assert len(result["segments"]) == 3
    # Window segments are shifted into absolute, strictly increasing time.
    starts = [segment["start"] for segment in result["segments"]]
    assert starts == sorted(starts)
    assert starts[0] == 0.0
    assert (
        starts[1]
        >= onnx_asr_engine.MAX_CHUNK_DURATION_S - onnx_asr_engine.CHUNK_SNAP_RADIUS_S
    )


def test_canary_passes_forced_language_to_recognizer(tmp_path, monkeypatch):
    from backend.processing.engines.canary_engine import CanaryEngine

    audio_path = tmp_path / "canary.wav"
    _write_silence(audio_path, 1)
    calls = []

    class _Recognizer:
        def recognize(self, path, **kwargs):
            calls.append((path, kwargs))
            return _FakeRecognized()

    class _Model:
        def with_timestamps(self):
            return _Recognizer()

    engine = CanaryEngine()
    monkeypatch.setattr(engine, "_get_model", lambda config: _Model())

    result = engine.transcribe(
        str(audio_path),
        {"transcription_language": "fr"},
    )

    assert calls == [(str(audio_path), {"language": "fr"})]
    assert result["language"] == "fr"


def test_parakeet_ignores_forced_language(tmp_path, monkeypatch):
    from backend.processing.engines.parakeet_engine import ParakeetEngine

    audio_path = tmp_path / "parakeet.wav"
    _write_silence(audio_path, 1)
    calls = []

    class _Recognizer:
        def recognize(self, path, **kwargs):
            calls.append((path, kwargs))
            return _FakeRecognized()

    class _Model:
        def with_timestamps(self):
            return _Recognizer()

    engine = ParakeetEngine()
    monkeypatch.setattr(engine, "_get_model", lambda config: _Model())

    result = engine.transcribe(
        str(audio_path),
        {"transcription_language": "fr"},
    )

    assert calls == [(str(audio_path), {})]
    assert result["language"] is None
