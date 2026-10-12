"""Stage-level characterization tests for ``process_recording_task``.

These tests pin the observable behaviour at the seams between the task's
explicit orchestration stages:

* input-audio resolution (proxy restore / repair / duration backfill),
* the VAD stage and its "no speech" short-circuit,
* the ASR stage telling an engine failure (ERROR, nothing downstream runs, one
  retry on GPU out-of-memory) apart from ASR that ran and heard nothing,
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
from backend.models.transcript import Transcript
from backend.processing import transcribe as real_transcribe
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
    # The real ownership rules for error_message, on this lightweight stand-in.
    transcription_failed = Transcript.transcription_failed
    fail_transcription = Transcript.fail_transcription
    complete_transcription = Transcript.complete_transcription
    set_notes_error_message = Transcript.set_notes_error_message

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
        EMBEDDING_METHOD_VERSION=2,
        release_embedding_model_cache=lambda: None,
    )
    _install(
        monkeypatch,
        "backend.processing.embedding",
        cosine_similarity=lambda *a, **k: 0.0,
        merge_embeddings=lambda *a, **k: None,
        find_matching_global_speaker=lambda *a, **k: (None, 0.0),
        embedding_version_of=lambda *a, **k: 2,
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
    # Same shape, same reason: the measured-analytics dispatches are resolved
    # from the package inside the success branch, and an unstubbed .delay()
    # opens a real broker connection.
    monkeypatch.setattr(
        tasks_module,
        "compute_delivery_analytics_task",
        types.SimpleNamespace(delay=lambda *a, **k: None),
        raising=False,
    )
    monkeypatch.setattr(
        tasks_module,
        "compute_overlap_analytics_task",
        types.SimpleNamespace(delay=lambda *a, **k: None),
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


# --- ASR failure vs. silence ---------------------------------------------------
#
# These run the real dispatcher and a real ParakeetEngine; only the onnx-asr
# model underneath is fake, so the engine's own error handling is exercised.

_PARAKEET = {"transcription_backend": "parakeet"}
_CUDA_OOM = (
    "[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : Non-zero status code returned "
    "while running MatMul node. CUDA failure 2: out of memory ; GPU=0"
)


class _Recognized:
    def __init__(self, text: str):
        self.text = text
        self.tokens = [f" {text}"] if text else []
        self.timestamps = [0.0] if text else []


def _route_asr_through_parakeet(monkeypatch, outcomes, *, on_gpu=False):
    """Serve each recognize() call from ``outcomes``: an exception is raised, a
    string is recognised as that text. Returns the list of recognised paths."""
    from backend.processing.engines import onnx_asr_engine
    from backend.processing.engines.parakeet_engine import ParakeetEngine

    calls: list[str] = []
    pending = list(outcomes)

    class _Recognizer:
        def recognize(self, path, **kwargs):
            calls.append(path)
            outcome = pending.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return _Recognized(outcome)

    class _Model:
        def with_timestamps(self):
            return _Recognizer()

    monkeypatch.setattr(onnx_asr_engine, "gpu_is_present", lambda: on_gpu)
    engine = ParakeetEngine()
    monkeypatch.setattr(engine, "_get_model", lambda config: _Model())
    monkeypatch.setitem(sys.modules, "backend.processing.transcribe", real_transcribe)
    monkeypatch.setitem(real_transcribe._ENGINE_REGISTRY, "parakeet", engine)
    return calls


def _record_downstream_stages(monkeypatch) -> dict[str, list]:
    """Record whether diarization, notes and post-processing follow-ups ran."""
    from backend.worker.tasks import followups

    ran: dict[str, list] = {"diarize": [], "notes": [], "followups": []}
    _install(
        monkeypatch,
        "backend.processing.diarize",
        diarize_audio=lambda *a, **k: ran["diarize"].append(a),
        release_pipeline_cache=lambda: None,
    )
    monkeypatch.setattr(
        tasks_module,
        "_run_automatic_meeting_intelligence_stage",
        lambda *a, **k: ran["notes"].append(k),
    )
    monkeypatch.setattr(
        followups,
        "dispatch_post_processing_followups",
        lambda recording_id: ran["followups"].append(recording_id),
    )
    return ran


def test_asr_engine_failure_marks_transcript_and_recording_failed(monkeypatch):
    """An engine error ends in ERROR with the reason, never an empty 'completed'
    transcript, and nothing downstream runs on the missing text."""
    recording = _FakeRecording(704)
    transcript = _FakeTranscript(704)
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(monkeypatch)
    _route_asr_through_parakeet(monkeypatch, [RuntimeError("decoder exploded")])
    ran = _record_downstream_stages(monkeypatch)

    result = _run_task(
        monkeypatch, session, recording_id=704, engine_override=_PARAKEET
    )

    message = "Transcription failed (parakeet): RuntimeError: decoder exploded"
    assert result is None
    assert recording.status == RecordingStatus.ERROR
    assert recording.processing_step == message
    assert recording.processing_completed_at is None
    assert transcript.transcript_status == "error"
    assert transcript.error_message == message
    assert transcript.text is None
    assert ran == {"diarize": [], "notes": [], "followups": []}


def test_asr_gpu_oom_is_retried_once_and_can_recover(monkeypatch):
    recording = _FakeRecording(706)
    transcript = _FakeTranscript(706)
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(monkeypatch)
    calls = _route_asr_through_parakeet(
        monkeypatch, [RuntimeError(_CUDA_OOM), "hello"], on_gpu=True
    )

    result = _run_task(
        monkeypatch, session, recording_id=706, engine_override=_PARAKEET
    )

    assert len(calls) == 2
    assert result == {"status": "success", "recording_id": 706}
    assert transcript.transcript_status == "completed"
    assert transcript.text == "hello"
    assert recording.processing_step == "Completed"


def test_asr_gpu_oom_twice_surfaces_cuda_out_of_memory(monkeypatch):
    recording = _FakeRecording(707)
    transcript = _FakeTranscript(707)
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(monkeypatch)
    calls = _route_asr_through_parakeet(
        monkeypatch, [RuntimeError(_CUDA_OOM), RuntimeError(_CUDA_OOM)], on_gpu=True
    )

    _run_task(monkeypatch, session, recording_id=707, engine_override=_PARAKEET)

    assert len(calls) == 2
    assert recording.status == RecordingStatus.ERROR
    assert transcript.transcript_status == "error"
    assert "GPU ran out of memory (CUDA out of memory)" in transcript.error_message


def test_asr_stage_lets_a_task_time_limit_through(monkeypatch):
    """A Celery soft time limit stops the task; it is not recorded as a failed
    transcription."""
    from celery.exceptions import SoftTimeLimitExceeded

    recording = _FakeRecording(713)
    transcript = _FakeTranscript(713)
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(monkeypatch)
    _route_asr_through_parakeet(monkeypatch, [SoftTimeLimitExceeded()])

    _run_task(monkeypatch, session, recording_id=713, engine_override=_PARAKEET)

    assert transcript.transcript_status == "pending"
    assert transcript.error_message is None
    assert recording.status == RecordingStatus.ERROR
    assert recording.processing_step.startswith("System Error")


def test_asr_failure_is_recorded_even_when_freeing_gpu_memory_fails(monkeypatch):
    """The OOM retry frees memory first. If that raises, the run still ends with
    a failed transcript and an ERROR recording, not one stuck PROCESSING
    because its transcript row still says "processing" (as imports create it)."""
    from backend.utils.status_manager import update_recording_status
    from backend.worker.tasks import pipeline as pipeline_module

    recording = _FakeRecording(711)
    transcript = _FakeTranscript(711)
    transcript.transcript_status = "processing"
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(monkeypatch)
    _route_asr_through_parakeet(monkeypatch, [RuntimeError(_CUDA_OOM)], on_gpu=True)
    monkeypatch.setattr(
        tasks_module, "update_recording_status", update_recording_status
    )

    def _driver_gone():
        raise RuntimeError("CUDA driver unavailable")

    monkeypatch.setattr(pipeline_module, "_release_pipeline_vram", _driver_gone)

    _run_task(monkeypatch, session, recording_id=711, engine_override=_PARAKEET)

    # The OOM is what the user needs to see, not the failure to free memory.
    assert transcript.transcript_status == "error"
    assert "GPU ran out of memory (CUDA out of memory)" in transcript.error_message
    assert recording.status == RecordingStatus.ERROR
    assert recording.processing_step == transcript.error_message


def test_asr_hearing_no_speech_is_a_completed_empty_transcript(monkeypatch):
    """VAD passed the audio but ASR recognised nothing: that is silence, not a
    failure, so the recording completes with an empty transcript."""
    recording = _FakeRecording(708)
    transcript = _FakeTranscript(708)
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(monkeypatch, segments=[])
    _route_asr_through_parakeet(monkeypatch, [""])

    result = _run_task(
        monkeypatch, session, recording_id=708, engine_override=_PARAKEET
    )

    assert result == {"status": "success", "recording_id": 708}
    assert recording.status != RecordingStatus.ERROR
    assert recording.processing_step == "Completed"
    assert transcript.transcript_status == "completed"
    assert transcript.text == ""
    assert transcript.segments == []
    assert transcript.error_message is None


def _transcript_left_failed_by_a_previous_run(recording_id: int) -> _FakeTranscript:
    transcript = _FakeTranscript(recording_id)
    transcript.transcript_status = "error"
    transcript.error_message = "Transcription failed: the GPU ran out of memory"
    return transcript


def _run_no_speech(monkeypatch, session, recording_id):
    _install_happy_path_modules(monkeypatch)
    _install(
        monkeypatch,
        "backend.processing.vad",
        mute_non_speech_segments=lambda *a, **k: (True, 0.0),
    )
    _run_task(monkeypatch, session, recording_id=recording_id)


def test_no_speech_short_circuit_clears_a_previous_failure(monkeypatch):
    recording = _FakeRecording(709)
    transcript = _transcript_left_failed_by_a_previous_run(709)
    session = _FakeSession(recording, transcript)

    _run_no_speech(monkeypatch, session, 709)

    assert recording.status == RecordingStatus.PROCESSED
    assert transcript.transcript_status == "completed"
    assert transcript.error_message is None
    # Nothing to summarise: notes stay as they were, the state the success path
    # also leaves when meeting intelligence skips an empty transcript.
    assert transcript.notes_status == "pending"


def test_no_speech_short_circuit_leaves_a_notes_error_alone(monkeypatch):
    """Outside a transcription failure, error_message belongs to notes."""
    recording = _FakeRecording(712)
    transcript = _FakeTranscript(712)
    transcript.transcript_status = "completed"
    transcript.notes_status = "error"
    transcript.error_message = "No model selected for anthropic"
    session = _FakeSession(recording, transcript)

    _run_no_speech(monkeypatch, session, 712)

    assert transcript.transcript_status == "completed"
    assert transcript.notes_status == "error"
    assert transcript.error_message == "No model selected for anthropic"


def test_successful_run_clears_a_previous_failure(monkeypatch):
    recording = _FakeRecording(710)
    transcript = _transcript_left_failed_by_a_previous_run(710)
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(monkeypatch)

    result = _run_task(monkeypatch, session, recording_id=710)

    assert result == {"status": "success", "recording_id": 710}
    assert transcript.transcript_status == "completed"
    assert transcript.error_message is None
    assert transcript.notes_status == "pending"


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
        embedding_version_of=lambda *a, **k: 2,
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
        embedding_version_of=lambda *a, **k: 2,
        AUTO_UPDATE_THRESHOLD=0.8,
    )
    _install(
        monkeypatch,
        "backend.processing.embedding_core",
        extract_embeddings=lambda *a, **k: {
            "SPEAKER_00": [0.1, 0.2],
            "SPEAKER_01": [0.3, 0.4],
        },
        EMBEDDING_METHOD_VERSION=2,
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
        embedding_version_of=lambda *a, **k: 2,
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
        EMBEDDING_METHOD_VERSION=2,
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


# --- meeting-intelligence lane routing -------------------------------------


def _arm_intelligence_routing_probes(monkeypatch):
    """Give the pipeline a non-empty transcript and capture whether the stage
    ran inline (GPU) or was dispatched to the IO lane."""
    monkeypatch.setattr(
        tasks_module,
        "_build_automatic_meeting_intelligence_transcript",
        lambda *a, **k: "[00:00 - 00:01] SPEAKER_00: hi",
    )
    inline_calls: list = []
    monkeypatch.setattr(
        tasks_module,
        "_run_automatic_meeting_intelligence_stage",
        lambda *a, **k: inline_calls.append(k),
    )
    dispatched: list = []
    monkeypatch.setattr(
        tasks_module.celery_app,
        "send_task",
        lambda name, *a, **k: dispatched.append((name, k.get("args"))),
    )
    return inline_calls, dispatched


def test_non_local_provider_defers_intelligence_to_io_lane(monkeypatch):
    """A cloud/CLI provider must not run the LLM call on the GPU worker: the
    pipeline dispatches generate_meeting_intelligence_task to the IO lane and the
    recording still completes (notes finish out-of-band)."""
    recording = _FakeRecording(710)
    transcript = _FakeTranscript(710)
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(
        monkeypatch,
        segments=[{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "hi"}],
    )
    inline_calls, dispatched = _arm_intelligence_routing_probes(monkeypatch)

    # Default _FakeLlmConfig uses provider="openai" (non-local, configured).
    result = _run_task(monkeypatch, session, recording_id=710)

    assert result == {"status": "success", "recording_id": 710}
    assert inline_calls == []  # never ran inline on the GPU worker
    assert dispatched == [
        ("backend.worker.tasks.generate_meeting_intelligence_task", [710])
    ]
    assert transcript.notes_status == "generating"
    assert recording.processing_step == "Completed"
    assert recording.processing_progress == 100


def test_local_provider_runs_intelligence_inline(monkeypatch):
    """Local Ollama stays inline on the GPU worker (no IO dispatch)."""
    recording = _FakeRecording(711)
    transcript = _FakeTranscript(711)
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(
        monkeypatch,
        segments=[{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "hi"}],
    )
    inline_calls, dispatched = _arm_intelligence_routing_probes(monkeypatch)

    class _OllamaConfig:
        merged_config = {"prefer_short_titles": True}
        provider = "ollama"

        def missing_configuration_message(self):
            return None

    result = _run_task(
        monkeypatch, session, recording_id=711, llm_config=_OllamaConfig()
    )

    assert result == {"status": "success", "recording_id": 711}
    assert dispatched == []  # not deferred
    assert len(inline_calls) == 1  # ran inline on the GPU worker


# --- user processing tuning reaches the stages -------------------------------


def _tuned_llm_config(**tuning):
    class _TunedLlmConfig:
        merged_config = {
            "transcription_backend": "whisper",
            "whisper_model_size": "base",
            "enable_vad": True,
            "enable_diarization": True,
            "enable_auto_voiceprints": False,
            "prefer_short_titles": True,
            **tuning,
        }
        provider = "openai"

        def missing_configuration_message(self):
            return None

    return _TunedLlmConfig()


def test_vad_stage_receives_the_owners_merged_settings(monkeypatch):
    """The owner's VAD threshold only takes effect if the stage hands the
    merged settings to the VAD call."""
    recording = _FakeRecording(720)
    transcript = _FakeTranscript(720)
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(monkeypatch)

    seen: dict = {}

    def _capture_vad(*args, **kwargs):
        seen["config"] = kwargs.get("config")
        return True, 30.0

    _install(
        monkeypatch, "backend.processing.vad", mute_non_speech_segments=_capture_vad
    )
    llm_config = _tuned_llm_config(vad_threshold=0.3)

    _run_task(monkeypatch, session, recording_id=720, llm_config=llm_config)

    assert seen["config"]["vad_threshold"] == 0.3


def test_combine_stage_receives_the_owners_merged_settings(monkeypatch):
    """The owner's flip-smoothing limits only take effect if the combine
    stage hands the merged settings to the combiner."""
    recording = _FakeRecording(721)
    transcript = _FakeTranscript(721)
    session = _FakeSession(recording, transcript)
    _install_happy_path_modules(monkeypatch, diarization_result=object())
    _install(
        monkeypatch,
        "backend.processing.phantom_filter",
        filter_phantom_speakers=lambda diar, *a, **k: diar,
    )

    seen: dict = {}

    def _capture_combine(transcription, diarization, config=None):
        seen["config"] = config
        return []

    _install(
        monkeypatch,
        "backend.utils.transcript_utils",
        combine_transcription_diarization=_capture_combine,
        consolidate_diarized_transcript=lambda segs, *a, **k: list(segs),
    )
    llm_config = _tuned_llm_config(word_flip_max_duration_s=0)

    _run_task(monkeypatch, session, recording_id=721, llm_config=llm_config)

    assert seen["config"]["word_flip_max_duration_s"] == 0
