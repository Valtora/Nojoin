"""Stage-level characterization tests for ``process_recording_task``.

These tests pin the CURRENT observable behaviour at the seams that the
BE-004 decomposition extracts into explicit orchestration stages:

* input-audio resolution (proxy restore / repair / duration backfill),
* the VAD stage and its "no speech" short-circuit,
* combine + consolidate of ASR and diarization into final segments
  (including the raw-transcription fallback that pins every segment to the
  ``UNKNOWN`` speaker while preserving ``id``/``words``),
* speaker assignment / identification, exercising the load-bearing
  invariants -- manual-edit authority (a manually renamed speaker is never
  re-identified) and stable-id alignment (duplicate resolved names auto-merge
  into the first speaker and the in-memory segments are rewritten to the
  canonical label),
* the overall success path and its status / progress transitions.

They drive the public bound task with light fakes so the assertions remain
deterministic without mocking the unit under test.
"""

from __future__ import annotations

import sys
import types

from backend.models.recording import ClientStatus, RecordingStatus
from backend.worker import tasks as tasks_module


def _install(monkeypatch, module_name: str, **attrs) -> None:
    """Install a stub module so the task's lazy heavy-ML imports resolve."""
    mod = types.ModuleType(module_name)
    for name, value in attrs.items():
        setattr(mod, name, value)
    monkeypatch.setitem(sys.modules, module_name, mod)


class _ExecResult:
    def __init__(self, first=None, all_=None):
        self._first = first
        self._all = all_ if all_ is not None else []

    def first(self):
        return self._first

    def all(self):
        return list(self._all)

    def with_for_update(self):
        return self


class _FakeTranscript:
    def __init__(self, recording_id: int):
        self.recording_id = recording_id
        self.text = None
        self.segments = None
        self.notes = None
        self.user_notes = None
        self.transcript_status = "pending"
        self.notes_status = "pending"
        self.error_message = None


class _FakeRecording:
    def __init__(self, recording_id: int):
        self.id = recording_id
        self.status = RecordingStatus.PROCESSED
        self.client_status = None
        self.user_id = None
        self.name = "Untitled"
        self.audio_path = "/tmp/recording.wav"
        self.proxy_path = None
        self.duration_seconds = 60.0
        self.processing_started_at = None
        self.processing_completed_at = None
        self.processing_progress = 0
        self.processing_step = ""
        self.calendar_event_id = None


class _FakeSession:
    """Minimal session capturing the rows the pipeline persists."""

    def __init__(self, recording: _FakeRecording, transcript: _FakeTranscript):
        self.recording = recording
        self.transcript = transcript
        self.added: list = []
        self.committed = 0
        self.speaker_rows: list = []
        self._speaker_seq = 9000

    def get(self, model, ident):
        if getattr(model, "__name__", "") == "Recording":
            return self.recording
        return None

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed += 1

    def refresh(self, obj):
        pass

    def flush(self):
        for obj in self.added:
            if obj.__class__.__name__ == "RecordingSpeaker" and obj.id is None:
                self._speaker_seq += 1
                obj.id = self._speaker_seq

    def close(self):
        pass

    def exec(self, statement=None, *args, **kwargs):
        # Transcript lookups return the shared transcript; speaker / global
        # speaker / manifest lookups are empty in the happy path.
        text = str(statement).lower()
        if "transcript" in text and "utterance" not in text:
            return _ExecResult(first=self.transcript, all_=[])
        return _ExecResult(first=None, all_=[])


def _install_happy_path_modules(monkeypatch, *, diarization_result=None, segments=None):
    """Install the heavy-import stubs shared by the success-path tests."""
    _install(
        monkeypatch,
        "backend.processing.audio_preprocessing",
        cleanup_temp_file=lambda *a, **k: None,
        convert_wav_to_mp3=lambda *a, **k: None,
        preprocess_audio_for_vad=lambda path: "/tmp/recording_vad.wav",
        repair_audio_file=lambda *a, **k: None,
        validate_audio_file=lambda *a, **k: None,
    )
    _install(
        monkeypatch,
        "backend.processing.vad",
        mute_non_speech_segments=lambda *a, **k: (True, 30.0),
    )
    _install(
        monkeypatch,
        "backend.processing.transcribe",
        transcribe_audio=lambda *a, **k: {
            "text": "hello world",
            "language": "en",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}],
        },
        release_model_cache=lambda: None,
    )
    _install(
        monkeypatch,
        "backend.processing.diarize",
        diarize_audio=lambda *a, **k: diarization_result,
        release_pipeline_cache=lambda: None,
    )
    _install(
        monkeypatch,
        "backend.processing.embedding_core",
        extract_embeddings=lambda *a, **k: {},
        release_embedding_model_cache=lambda: None,
    )
    _install(
        monkeypatch,
        "backend.processing.embedding",
        cosine_similarity=lambda *a, **k: 0.0,
        merge_embeddings=lambda *a, **k: None,
        find_matching_global_speaker=lambda *a, **k: (None, 0.0),
        AUTO_UPDATE_THRESHOLD=0.8,
    )
    _install(
        monkeypatch,
        "backend.utils.transcript_utils",
        combine_transcription_diarization=lambda *a, **k: [],
        consolidate_diarized_transcript=lambda segs, *a, **k: list(
            segments if segments is not None else []
        ),
    )
    _install(
        monkeypatch,
        "backend.utils.audio",
        convert_to_mp3=lambda *a, **k: None,
        convert_to_proxy_mp3=lambda *a, **k: None,
        get_audio_duration=lambda *a, **k: 60.0,
        extract_audio_clip=lambda *a, **k: None,
        convert_to_wav=lambda *a, **k: True,
    )
    _install(
        monkeypatch,
        "backend.utils.live_transcript",
        apply_live_authority_to_segments=lambda live, combined: combined,
        build_transcription_result_from_segments=lambda segs: (
            {"text": "", "segments": []},
            [],
        ),
        map_final_speakers_to_live_labels=lambda *a, **k: {},
        merge_reusable_segments=lambda primary, additional: (
            list(primary) + list(additional)
        ),
    )
    _install(
        monkeypatch,
        "backend.processing.text_embedding",
        release_embedding_model=lambda: None,
    )
    _install(
        monkeypatch,
        "backend.processing.segmentation_refinement",
        release_segmentation_model_cache=lambda: None,
    )

    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr(tasks_module.config_manager, "reload", lambda: None)

    def _config_get(key, default=None):
        # Drive the legacy (non-canonical, no-ledger) path so these seam tests
        # exercise combine/consolidate + speaker assignment rather than the
        # canonical-write and ASR-ledger branches.
        if key == "keep_models_loaded":
            return True
        if key in {
            "enable_canonical_transcript_writes",
            "enable_asr_window_result_ledger",
        }:
            return False
        return default

    monkeypatch.setattr(tasks_module.config_manager, "get", _config_get)
    # Keep the canonical-write and ledger branches out of the success path so the
    # assertions focus on the combine/consolidate + speaker-assignment seams.
    monkeypatch.setattr(
        tasks_module, "build_reusable_live_segments", lambda *a, **k: []
    )
    monkeypatch.setattr(tasks_module, "auto_link_recording", lambda *a, **k: None)
    monkeypatch.setattr(tasks_module, "update_recording_status", lambda *a, **k: None)
    monkeypatch.setattr(
        tasks_module,
        "mark_recording_audio_chunks_ready_for_cleanup",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(tasks_module, "build_recording_speaker_map", lambda *a, **k: {})
    monkeypatch.setattr(
        tasks_module, "get_speakers_eligible_for_llm_renaming", lambda *a, **k: []
    )
    monkeypatch.setattr(
        tasks_module,
        "_build_automatic_meeting_intelligence_transcript",
        lambda *a, **k: "",
    )
    monkeypatch.setattr(
        tasks_module, "_run_automatic_meeting_intelligence_stage", lambda *a, **k: None
    )

    index_calls: list = []
    # index_transcript_task is imported from backend.worker.tasks inside the
    # success branch; patch the attribute the task resolves.
    monkeypatch.setattr(
        tasks_module,
        "index_transcript_task",
        types.SimpleNamespace(delay=lambda *a, **k: index_calls.append(a)),
        raising=False,
    )
    return index_calls


def _run_task(
    monkeypatch, session, recording_id=701, engine_override=None, llm_config=None
):
    if llm_config is None:

        class _FakeLlmConfig:
            merged_config = {
                "transcription_backend": "whisper",
                "whisper_model_size": "base",
                "enable_vad": True,
                "enable_diarization": True,
                "enable_auto_voiceprints": False,
                "prefer_short_titles": True,
            }
            provider = "openai"

            def missing_configuration_message(self):
                return None

        llm_config = _FakeLlmConfig()

    monkeypatch.setattr(tasks_module, "resolve_llm_config", lambda *a, **k: llm_config)

    task = tasks_module.process_recording_task
    monkeypatch.setattr(task, "_session", session, raising=False)
    monkeypatch.setattr(task, "update_state", lambda *a, **k: None, raising=False)
    return task.run(recording_id, False, engine_override)


# --- combine / consolidate seam --------------------------------------------


def test_success_path_persists_consolidated_segments_and_completes(monkeypatch):
    """End-to-end happy path: status PROCESSED via Completed, progress 100,
    transcript text + consolidated segments persisted."""
    recording = _FakeRecording(701)
    transcript = _FakeTranscript(701)
    session = _FakeSession(recording, transcript)

    consolidated = [
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "hello world"}
    ]
    _install_happy_path_modules(monkeypatch, segments=consolidated)
    # Diarization disabled-shape: combine returns [] so the fallback path runs,
    # but here we feed consolidate directly via the stub.

    result = _run_task(monkeypatch, session)

    assert result == {"status": "success", "recording_id": 701}
    assert transcript.text == "hello world"
    assert transcript.transcript_status == "completed"
    assert transcript.segments == consolidated
    assert recording.client_status == ClientStatus.IDLE
    assert recording.processing_step == "Completed"
    assert recording.processing_progress == 100
    assert recording.processing_completed_at is not None


def test_no_speech_short_circuits_to_processed_with_empty_transcript(monkeypatch):
    """The VAD stage's <1s speech short-circuit yields PROCESSED with an empty
    transcript (empty string + empty segments) and never invokes ASR."""
    recording = _FakeRecording(702)
    transcript = _FakeTranscript(702)
    session = _FakeSession(recording, transcript)

    _install_happy_path_modules(monkeypatch)

    def _exploding_transcribe(*args, **kwargs):
        raise AssertionError("ASR must not run when no speech is detected")

    _install(
        monkeypatch,
        "backend.processing.transcribe",
        transcribe_audio=_exploding_transcribe,
        release_model_cache=lambda: None,
    )
    _install(
        monkeypatch,
        "backend.processing.vad",
        mute_non_speech_segments=lambda *a, **k: (True, 0.0),
    )

    result = _run_task(monkeypatch, session, recording_id=702)

    assert result is None
    assert recording.status == RecordingStatus.PROCESSED
    assert recording.client_status == ClientStatus.IDLE
    assert recording.processing_step == "Completed (No speech detected)"
    assert transcript.text == ""
    assert transcript.segments == []
    assert transcript.transcript_status == "completed"


def test_raw_transcription_fallback_pins_unknown_and_preserves_fields(monkeypatch):
    """When combination is skipped (no diarization), the fallback emits one
    segment per ASR segment pinned to UNKNOWN, preserving id and words."""
    recording = _FakeRecording(703)
    transcript = _FakeTranscript(703)
    session = _FakeSession(recording, transcript)

    captured: dict = {}

    def _capture_consolidate(segs, *a, **k):
        captured["combined"] = [dict(s) for s in segs]
        return list(segs)

    _install_happy_path_modules(monkeypatch)
    _install(
        monkeypatch,
        "backend.utils.transcript_utils",
        combine_transcription_diarization=lambda *a, **k: [],
        consolidate_diarized_transcript=_capture_consolidate,
    )
    _install(
        monkeypatch,
        "backend.processing.transcribe",
        transcribe_audio=lambda *a, **k: {
            "text": "alpha beta",
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "text": " alpha ",
                    "id": "seg-1",
                    "words": [{"w": "alpha"}],
                }
            ],
        },
        release_model_cache=lambda: None,
    )
    # Diarization returns None so combine is skipped -> raw fallback.
    _install(
        monkeypatch,
        "backend.processing.diarize",
        diarize_audio=lambda *a, **k: None,
        release_pipeline_cache=lambda: None,
    )

    _run_task(monkeypatch, session, recording_id=703)

    assert captured["combined"] == [
        {
            "start": 0.0,
            "end": 1.0,
            "speaker": "UNKNOWN",
            "text": "alpha",
            "id": "seg-1",
            "words": [{"w": "alpha"}],
        }
    ]


def test_none_transcription_result_yields_empty_full_text(monkeypatch):
    """If ASR returns None, the persisted transcript text is the empty string."""
    recording = _FakeRecording(704)
    transcript = _FakeTranscript(704)
    session = _FakeSession(recording, transcript)

    _install_happy_path_modules(monkeypatch, segments=[])
    _install(
        monkeypatch,
        "backend.processing.transcribe",
        transcribe_audio=lambda *a, **k: None,
        release_model_cache=lambda: None,
    )
    _install(
        monkeypatch,
        "backend.processing.diarize",
        diarize_audio=lambda *a, **k: None,
        release_pipeline_cache=lambda: None,
    )

    _run_task(monkeypatch, session, recording_id=704)

    assert transcript.text == ""
    assert transcript.segments == []


# --- speaker assignment seam: manual-edit authority & stable-id alignment ----


def _make_speaker_session(recording, transcript, existing_speakers, global_speakers):
    class _SpeakerSession(_FakeSession):
        def __init__(self):
            super().__init__(recording, transcript)
            self._existing = list(existing_speakers)
            self._globals = list(global_speakers)

        def exec(self, statement, *args, **kwargs):
            text = str(statement).lower()
            if "globalspeaker" in text or "global_speaker" in text:
                return _ExecResult(all_=self._globals)
            if "recordingspeaker" in text or "recording_speaker" in text:
                # Per-label lookup uses .first(); the bulk reload uses .all().
                return _ExecResult(
                    first=self._existing[0] if self._existing else None,
                    all_=self._existing,
                )
            return _ExecResult(first=self.transcript, all_=[])

    return _SpeakerSession()


def test_manual_name_is_preserved_and_not_reidentified(monkeypatch):
    """Manual-edit authority: a speaker with a local_name keeps it and is never
    matched against global speakers (find_matching_global_speaker not called)."""
    recording = _FakeRecording(705)
    transcript = _FakeTranscript(705)

    existing = types.SimpleNamespace(
        id=55,
        diarization_label="SPEAKER_00",
        local_name="Alice (manual)",
        name="Alice (manual)",
        merged_into_id=None,
        global_speaker_id=None,
        embedding=None,
    )
    session = _make_speaker_session(recording, transcript, [existing], [])

    consolidated = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "hi"}]
    _install_happy_path_modules(monkeypatch, segments=consolidated)

    def _must_not_match(*args, **kwargs):
        raise AssertionError(
            "manual-named speaker must not be re-identified against globals"
        )

    _install(
        monkeypatch,
        "backend.processing.embedding",
        cosine_similarity=lambda *a, **k: 0.0,
        merge_embeddings=lambda *a, **k: None,
        find_matching_global_speaker=_must_not_match,
        AUTO_UPDATE_THRESHOLD=0.8,
    )

    result = _run_task(monkeypatch, session, recording_id=705)

    assert result == {"status": "success", "recording_id": 705}
    assert existing.name == "Alice (manual)"


def test_duplicate_resolved_name_auto_merges_and_rewrites_segments(monkeypatch):
    """Stable-id alignment: two labels resolving to the same global name merge
    into the first, and the in-memory segments are rewritten to the canonical
    target label."""
    recording = _FakeRecording(706)
    transcript = _FakeTranscript(706)
    session = _FakeSession(recording, transcript)

    consolidated = [
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "a"},
        {"start": 1.0, "end": 2.0, "speaker": "SPEAKER_01", "text": "b"},
    ]
    # A truthy diarization result gates embedding extraction (and therefore
    # global identification) on. Phantom filter + speaker_merge are best-effort.
    _install_happy_path_modules(
        monkeypatch, diarization_result=object(), segments=consolidated
    )
    _install(
        monkeypatch,
        "backend.processing.phantom_filter",
        filter_phantom_speakers=lambda diar, *a, **k: diar,
    )
    _install(
        monkeypatch,
        "backend.processing.speaker_merge",
        merge_duplicate_speakers=lambda *a, **k: [],
    )
    # combine yields the labelled segments; consolidate passes them through.
    _install(
        monkeypatch,
        "backend.utils.transcript_utils",
        combine_transcription_diarization=lambda *a, **k: [
            dict(s) for s in consolidated
        ],
        consolidate_diarized_transcript=lambda segs, *a, **k: list(segs),
    )
    # Voiceprints on so embeddings exist for matching.
    monkeypatch.setattr(tasks_module, "build_recording_speaker_map", lambda *a, **k: {})

    class _LlmConfig:
        merged_config = {
            "transcription_backend": "whisper",
            "whisper_model_size": "base",
            "enable_vad": True,
            "enable_diarization": True,
            "enable_auto_voiceprints": True,
            "prefer_short_titles": True,
        }
        provider = "openai"

        def missing_configuration_message(self):
            return None

    matched = types.SimpleNamespace(
        id=7,
        name="Dave",
        embedding=[0.1, 0.2],
        is_voiceprint_locked=True,
    )
    _install(
        monkeypatch,
        "backend.processing.embedding",
        cosine_similarity=lambda *a, **k: 0.0,
        merge_embeddings=lambda *a, **k: None,
        find_matching_global_speaker=lambda *a, **k: (matched, 0.99),
        AUTO_UPDATE_THRESHOLD=0.8,
    )
    _install(
        monkeypatch,
        "backend.processing.embedding_core",
        extract_embeddings=lambda *a, **k: {
            "SPEAKER_00": [0.1, 0.2],
            "SPEAKER_01": [0.3, 0.4],
        },
        release_embedding_model_cache=lambda: None,
    )

    # Both labels match the same global speaker "Dave"; second must merge into
    # first and the second segment's label must be rewritten to SPEAKER_00.
    final_segments_seen: dict = {}

    def _capture(updated_segments, *a, **k):
        final_segments_seen["segments"] = [dict(s) for s in updated_segments]
        return ""

    monkeypatch.setattr(
        tasks_module, "_build_automatic_meeting_intelligence_transcript", _capture
    )

    result = _run_task(monkeypatch, session, recording_id=706, llm_config=_LlmConfig())

    assert result == {"status": "success", "recording_id": 706}
    rewritten = final_segments_seen["segments"]
    assert [seg["speaker"] for seg in rewritten] == ["SPEAKER_00", "SPEAKER_00"]


def test_unidentified_speakers_get_sequential_names(monkeypatch):
    """With no global match and no manual name, speakers receive sequential
    'Speaker N' names in order of appearance."""
    recording = _FakeRecording(707)
    transcript = _FakeTranscript(707)
    session = _FakeSession(recording, transcript)

    consolidated = [
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "a"},
        {"start": 1.0, "end": 2.0, "speaker": "SPEAKER_01", "text": "b"},
    ]
    _install_happy_path_modules(
        monkeypatch, diarization_result=object(), segments=consolidated
    )
    _install(
        monkeypatch,
        "backend.processing.phantom_filter",
        filter_phantom_speakers=lambda diar, *a, **k: diar,
    )
    _install(
        monkeypatch,
        "backend.utils.transcript_utils",
        combine_transcription_diarization=lambda *a, **k: [
            dict(s) for s in consolidated
        ],
        consolidate_diarized_transcript=lambda segs, *a, **k: list(segs),
    )
    # No global match -> sequential naming.
    _install(
        monkeypatch,
        "backend.processing.embedding",
        cosine_similarity=lambda *a, **k: 0.0,
        merge_embeddings=lambda *a, **k: None,
        find_matching_global_speaker=lambda *a, **k: (None, 0.0),
        AUTO_UPDATE_THRESHOLD=0.8,
    )

    created_names: list = []

    class _CapturingSession(_FakeSession):
        def add(self, obj):
            super().add(obj)
            if obj.__class__.__name__ == "RecordingSpeaker":
                created_names.append(getattr(obj, "name", None))

    session = _CapturingSession(recording, transcript)

    class _LlmConfig:
        merged_config = {
            "transcription_backend": "whisper",
            "whisper_model_size": "base",
            "enable_vad": True,
            "enable_diarization": True,
            "enable_auto_voiceprints": True,
            "prefer_short_titles": True,
        }
        provider = "openai"

        def missing_configuration_message(self):
            return None

    _install(
        monkeypatch,
        "backend.processing.embedding_core",
        extract_embeddings=lambda *a, **k: {
            "SPEAKER_00": [0.1],
            "SPEAKER_01": [0.2],
        },
        release_embedding_model_cache=lambda: None,
    )

    # speaker_merge import lives inside the task; stub it to a no-op.
    _install(
        monkeypatch,
        "backend.processing.speaker_merge",
        merge_duplicate_speakers=lambda *a, **k: [],
    )

    result = _run_task(monkeypatch, session, recording_id=707, llm_config=_LlmConfig())

    assert result == {"status": "success", "recording_id": 707}
    assert created_names == ["Speaker 1", "Speaker 2"]
