# Sequence-gated live transcription with completed-region and rolling word lanes.

import logging
import os
from typing import Any

from backend.celery_app import celery_app
from backend.processing.browser_live_audio import (
    BROWSER_LIVE_MICROPHONE_SOURCE,
    LIVE_SOURCE_AUTHORITY_CLEAR,
)
from backend.processing.engines.errors import TranscriptionError
from backend.processing.live_asr_payload import (
    _build_live_confidence_payload,
    _build_live_utterance_public_id,
    _prepare_live_region,
)
from backend.processing.live_audio_buffer import (
    LIVE_SAMPLE_RATE,
    _analyze_live_source_channels,
    _build_live_combined_buffer,
    _build_live_source_channel_evidence,
    _expand_audio_channels,
    _mix_live_audio_channels,
    _read_live_audio_channels,
    stage_live_audio,
)
from backend.processing.live_diarization.setting import live_speaker_labels_requested
from backend.processing.live_limits import (
    DEFAULT_FORCED_MAX,
    DEFAULT_MAX_SEGMENT_S,
    LIVE_MIN_SILENCE_MS,
)
from backend.processing.live_persistence import (
    _load_recording_audio_chunks as _load_recording_audio_chunks,
)
from backend.processing.live_persistence import (
    _load_recording_audio_window_manifests,
    _persist_live_run,
    logger,
)
from backend.processing.live_settling import SettlingStep, run_settling_step
from backend.processing.live_state import (
    _STATE_SOURCE_CHANNEL_LABELS_KEY,
    LIVE_INITIAL_SEQUENCE,
    _live_lane_has_consumed,
    _record_live_sequence_outcome,
    read_live_state,
    write_live_state,
)
from backend.processing.pipeline_metrics import (
    pipeline_metric_timer,
    record_pipeline_metric,
)
from backend.processing.processing_tuning import (
    ASR_WORD_LENGTH_CAP_KEY,
    LINE_BREAK_PAUSE_KEY,
    VAD_THRESHOLD_KEY,
    normalise_tuning_value,
)
from backend.utils.asr_config import build_asr_config_hash, get_transcription_model_name
from backend.utils.audio_windows import infer_resume_state_from_manifests
from backend.utils.config_manager import config_manager
from backend.utils.recording_storage import recording_upload_temp_dir
from backend.utils.transcript_settling import capture_publication_token

# Tolerance (seconds) for treating a speech region as touching the buffer end.
TRAIL_EPS = 0.20
_BUFFER_FILENAME = "buffer.wav"
_CONTEXT_FILENAME = "context.wav"


def _record_live_sequence_outcome_metric(
    *,
    recording_id: int,
    sequence: int,
    outcome: str,
    reason: str,
    run: list[int] | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "sequence": int(sequence),
        "outcome": outcome,
        "reason": reason,
        "is_first_sequence": int(sequence) == LIVE_INITIAL_SEQUENCE,
    }
    if run:
        payload["run"] = [int(item) for item in run]
    if extra_payload:
        payload.update(extra_payload)
    record_pipeline_metric(
        stage="live_sequence_outcome",
        recording_id=recording_id,
        payload=payload,
        status="error" if outcome == "failed" else outcome,
        log=logger,
    )


def _write_live_state_best_effort(live_dir, state: dict) -> None:
    try:
        write_live_state(live_dir, state)
    except OSError:
        logger.warning("Failed to persist live state", exc_info=True)


def classify_speech(
    speech: list[dict],
    combined_len: float,
    *,
    forced_max_s: float = DEFAULT_FORCED_MAX,
    max_segment_s: float = DEFAULT_MAX_SEGMENT_S,
) -> tuple[list[dict], float]:
    """Split detected speech regions into completed regions and a carry-over cut point.

    Returns (complete_segments, cut_point) where cut_point is the buffer offset
    (seconds) from which unconsumed audio should be carried into the next run.
    """
    if not speech:
        # No speech: drop the silent buffer entirely.
        return [], combined_len

    last = speech[-1]
    trailing_incomplete = last["end"] >= combined_len - TRAIL_EPS

    if trailing_incomplete and (combined_len - last["start"]) >= forced_max_s:
        # Trailing utterance has run too long; treat it as complete now.
        complete_regions = speech
        cut_point = combined_len
    elif trailing_incomplete:
        # Carry the trailing utterance forward from its start.
        complete_regions = speech[:-1]
        cut_point = last["start"]
    else:
        # Last region ended with silence; everything is complete.
        complete_regions = speech
        cut_point = last["end"]

    return _split_complete_regions(
        complete_regions, max_segment_s=max_segment_s
    ), cut_point


def _split_complete_regions(
    regions: list[dict],
    *,
    max_segment_s: float,
) -> list[dict]:
    if max_segment_s <= 0:
        return regions

    split_regions: list[dict] = []
    for region in regions:
        split_regions.extend(_split_region(region, max_segment_s=max_segment_s))
    return split_regions


def _split_region(region: dict, *, max_segment_s: float) -> list[dict]:
    start = float(region["start"])
    end = float(region["end"])
    duration = end - start

    if duration <= max_segment_s:
        return [region]

    split_regions: list[dict] = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(end, chunk_start + max_segment_s)
        chunk = dict(region)
        chunk["start"] = chunk_start
        chunk["end"] = chunk_end
        split_regions.append(chunk)
        chunk_start = chunk_end

    return split_regions


def _build_live_config() -> dict:
    """Build a minimal config dict for the live transcription engine call."""
    backend = config_manager.get("transcription_backend", "whisper")
    return {
        "transcription_backend": backend,
        "parakeet_model": config_manager.get("parakeet_model", "parakeet-tdt-0.6b-v3"),
        "canary_model": config_manager.get("canary_model", "nemo-canary-1b-v2"),
        "whisper_model_size": config_manager.get("whisper_model_size", "turbo"),
        "transcription_language": config_manager.get("transcription_language", "auto"),
        "processing_device": config_manager.get("processing_device", "auto"),
        "context_window_s": config_manager.get("live_context_window_s", 5.0),
        "forced_max_s": config_manager.get("live_forced_max_s", DEFAULT_FORCED_MAX),
        "max_segment_s": config_manager.get(
            "live_max_segment_s", DEFAULT_MAX_SEGMENT_S
        ),
        "speech_pad_ms": config_manager.get("live_speech_pad_ms", 300),
        LINE_BREAK_PAUSE_KEY: config_manager.get(LINE_BREAK_PAUSE_KEY),
    }


def _get_speaker_display_name(speaker: Any) -> str:
    global_speaker = getattr(speaker, "global_speaker", None)
    return (
        getattr(speaker, "local_name", None)
        or (getattr(global_speaker, "name", None) if global_speaker else None)
        or getattr(speaker, "name", None)
        or "Speaker 1"
    )


def _live_tuning_value(
    recording_id: int, merged_config: dict, key: str, *, warn: bool
) -> float | None:
    """The owner's ``key`` when usable, else None, so the reader inherits.

    Validated here rather than by the reader, which would log an unusable value
    on every live segment; ``warn`` lets the caller log it once per recording.
    """
    raw = merged_config.get(key)
    value = normalise_tuning_value(key, raw)
    if raw is not None and value is None:
        logger.log(
            logging.WARNING if warn else logging.DEBUG,
            "Ignoring invalid %s=%r for live recording %s.",
            key,
            raw,
            recording_id,
        )
    return value


def _resolve_live_engine_config(
    recording_id: int, live_config: dict, *, warn_unusable: bool = True
) -> dict:
    """Merge owner settings into live_config; absent owners keep the base config.

    Local imports avoid loading ML dependencies. ``warn_unusable`` selects
    WARNING over DEBUG until a live run of the recording has completed.
    """
    from backend.core.db import get_sync_session
    from backend.models.recording import Recording
    from backend.models.user import User
    from backend.worker.tasks import resolve_llm_config

    session = get_sync_session()
    try:
        recording = session.get(Recording, recording_id)
        if recording:
            user_id = getattr(recording, "user_id", None)
            user_settings = {}
            if user_id:
                user = session.get(User, user_id)
                if user and user.settings:
                    user_settings = user.settings
            if hasattr(session, "exec"):
                merged_config = resolve_llm_config(session, user_settings).merged_config
            else:
                merged_config = live_config
            merged_config.setdefault(
                "transcription_backend", live_config["transcription_backend"]
            )
            live_config.update(
                {
                    "transcription_backend": merged_config.get(
                        "transcription_backend",
                        live_config["transcription_backend"],
                    ),
                    "parakeet_model": merged_config.get(
                        "parakeet_model",
                        live_config["parakeet_model"],
                    ),
                    "canary_model": merged_config.get(
                        "canary_model",
                        live_config["canary_model"],
                    ),
                    "whisper_model_size": merged_config.get(
                        "whisper_model_size",
                        live_config["whisper_model_size"],
                    ),
                    "transcription_language": merged_config.get(
                        "transcription_language",
                        live_config["transcription_language"],
                    ),
                    "processing_device": merged_config.get(
                        "processing_device",
                        live_config["processing_device"],
                    ),
                    "forced_max_s": merged_config.get(
                        "live_forced_max_s",
                        live_config["forced_max_s"],
                    ),
                    "max_segment_s": merged_config.get(
                        "live_max_segment_s",
                        live_config["max_segment_s"],
                    ),
                    LINE_BREAK_PAUSE_KEY: _live_tuning_value(
                        recording_id,
                        merged_config,
                        LINE_BREAK_PAUSE_KEY,
                        warn=warn_unusable,
                    ),
                    "vad_threshold": _live_tuning_value(
                        recording_id,
                        merged_config,
                        VAD_THRESHOLD_KEY,
                        warn=warn_unusable,
                    ),
                    "asr_word_length_cap_s": _live_tuning_value(
                        recording_id,
                        merged_config,
                        ASR_WORD_LENGTH_CAP_KEY,
                        warn=warn_unusable,
                    ),
                }
            )
    finally:
        session.close()
    return live_config


def _transcribe_live_regions(
    *,
    recording_id: int,
    sequence: int,
    complete: list[dict],
    combined,
    combined_channels,
    combined_abs_start: float,
    prev_context,
    context_window_samples: int,
    live_dir,
    live_config: dict,
    source_channel_labels: dict,
    state: dict,
) -> list[dict]:
    """Transcribe each completed speech region into provisional live segments.

    Mutates ``state`` and ``source_channel_labels`` to carry the last
    stable speaker label forward. Heavy ML inference imports (silero_vad, the
    engine via transcribe_audio) stay inside this worker-only helper so module
    import time stays light.
    """
    import torch

    from backend.processing.transcribe import (
        loaded_transcription_device,
        transcribe_audio,
    )

    W = context_window_samples
    new_segments: list[dict] = []
    for sp in complete:
        start_sample = int(sp["start"] * LIVE_SAMPLE_RATE)
        end_sample = int(sp["end"] * LIVE_SAMPLE_RATE)
        region = combined[start_sample:end_sample]
        region_channels = combined_channels[:, start_sample:end_sample]
        if region.numel() == 0:
            continue
        source_activity = _analyze_live_source_channels(region_channels)
        source_channel_evidence = _build_live_source_channel_evidence(
            source_activity,
            source_channel_labels,
        )
        logger.info(
            (
                "Live speech region source analysis for recording %s sequence %s "
                "region_start_ms=%s region_end_ms=%s source_activity=%s "
                "source_channel_evidence=%s"
            ),
            recording_id,
            sequence,
            int(round((combined_abs_start + sp["start"]) * 1000.0)),
            int(round((combined_abs_start + sp["end"]) * 1000.0)),
            source_activity,
            source_channel_evidence.to_payload(),
        )

        # Prepend a rolling audio context window so the engine has run-up.
        left_context = torch.cat([prev_context, combined[:start_sample]])
        if W > 0:
            left_context = left_context[-W:]
        else:
            left_context = left_context[:0]
        clip = torch.cat([left_context, region])
        prefix_s = left_context.numel() / LIVE_SAMPLE_RATE
        region_start_ms = int(round((combined_abs_start + sp["start"]) * 1000.0))
        region_end_ms = int(round((combined_abs_start + sp["end"]) * 1000.0))

        clip_path = str(live_dir / "clip.wav")
        try:
            from backend.processing.vad import silero_vad

            tensor = clip if clip.ndim > 1 else clip.unsqueeze(0)
            silero_vad.save_audio(clip_path, tensor, sampling_rate=LIVE_SAMPLE_RATE)
            with pipeline_metric_timer(
                stage="live_asr_region",
                recording_id=recording_id,
                payload={
                    "sequence": sequence,
                    "region_start_s": round(combined_abs_start + sp["start"], 3),
                    "region_end_s": round(combined_abs_start + sp["end"], 3),
                    "prefix_s": round(prefix_s, 3),
                    "engine": live_config.get("transcription_backend"),
                },
                log=logger,
            ) as metric:
                result = transcribe_audio(clip_path, config=live_config)
                device = loaded_transcription_device(live_config)
                if device is not None:
                    live_config["_settling_device"] = device
                metric["payload"]["text_chars"] = len(result.get("text") or "")
            speaker_label = "UNKNOWN"

        except TranscriptionError:
            # The engine failed on this region, and the failure is logged. Lose
            # this region only so the lane keeps moving: the final pass
            # transcribes the whole recording again.
            continue
        finally:
            if os.path.exists(clip_path):
                try:
                    os.remove(clip_path)
                except OSError:
                    pass

        text, region_segment_payloads = _prepare_live_region(result, prefix_s)
        if not text:
            continue

        segment_public_id = _build_live_utterance_public_id(
            recording_id=recording_id,
            span_start_ms=region_start_ms,
            span_end_ms=region_end_ms,
            speaker_label=speaker_label,
            text=text,
        )

        new_segments.append(
            {
                "id": segment_public_id,
                "start": combined_abs_start + sp["start"],
                "end": combined_abs_start + sp["end"],
                "speaker": speaker_label,
                "text": text,
                "provisional": True,
                "segment_source": "live",
                "speaker_state": "provisional",
                "speaker_confidence": source_channel_evidence.speaker_confidence,
                "confidence_payload": _build_live_confidence_payload(
                    region_segment_payloads=region_segment_payloads,
                    region_start_ms=region_start_ms,
                    region_end_ms=region_end_ms,
                    source_activity=source_activity,
                    source_channel_evidence=source_channel_evidence,
                ),
            }
        )
        if speaker_label != "UNKNOWN":
            state["last_speaker_label"] = speaker_label
            if (
                source_channel_evidence.authority == LIVE_SOURCE_AUTHORITY_CLEAR
                and source_channel_evidence.dominant_source
                == BROWSER_LIVE_MICROPHONE_SOURCE
            ):
                source_channel_labels.setdefault(
                    BROWSER_LIVE_MICROPHONE_SOURCE, speaker_label
                )

    return new_segments


def _dispatch_meeting_edge_refresh_best_effort(recording_id: int) -> None:
    """Enqueue a Meeting Edge refresh, swallowing dispatch failures.

    Best-effort: a broker/dispatch failure must not crash the live task. The
    final pipeline still drives downstream diarisation/edge work regardless.
    """
    try:
        from backend.worker.tasks import refresh_meeting_edge_task

        refresh_meeting_edge_task.delay(recording_id)
    except Exception as exc:  # noqa: BLE001 -- boundary: dispatch is best-effort
        logger.warning(
            "Failed to dispatch Meeting Edge refresh for recording %s: %s",
            recording_id,
            exc,
        )


@celery_app.task(bind=True, max_retries=None)
def transcribe_segment_live_task(self, recording_id: int, sequence: int) -> None:
    """Serialise drains across workers sharing the recording's audio."""
    from backend.processing.live_drain_lock import drain_live_recording

    drain_live_recording(self, recording_id, sequence)


def _transcribe_segment_live(recording_id: int, sequence: int) -> None:
    """Drain retained chunks; ordinary failures advance for final recovery."""
    import torch

    from backend.core.db import get_sync_session
    from backend.models.recording import Recording, RecordingStatus
    from backend.processing.live_run_failure import (
        LiveRun,
        record_live_run_failure,
    )
    from backend.processing.vad import detect_speech_segments

    record_pipeline_metric(
        stage="live_task_started",
        recording_id=recording_id,
        payload={"sequence": sequence},
        log=logger,
    )

    # The tail uploaded at a pause still runs; a rolling episode locks it as it lands.
    # Final/terminal states reject queued work even while chunk files remain.
    session = get_sync_session()
    try:
        recording = session.get(Recording, recording_id)
        if not recording or recording.status not in {
            RecordingStatus.UPLOADING,
            RecordingStatus.PAUSED,
        }:
            record_pipeline_metric(
                stage="live_task_skipped",
                recording_id=recording_id,
                payload={
                    "sequence": sequence,
                    "reason": "not_uploading",
                    "status": getattr(recording, "status", None),
                },
                status="skipped",
                log=logger,
            )
            _record_live_sequence_outcome_metric(
                recording_id=recording_id,
                sequence=sequence,
                outcome="skipped",
                reason="not_uploading",
                extra_payload={"status": getattr(recording, "status", None)},
            )
            return
        expected_token = capture_publication_token(
            recording, getattr(recording, "transcript", None)
        )
        settling_state = (
            getattr(getattr(recording, "transcript", None), "settling_state", None)
            or {}
        )
        settling_capture = bool(
            settling_state.get("eligible")
            and settling_state.get("policy", {}).get("enabled") is not False
        )
    finally:
        session.close()

    temp_dir = recording_upload_temp_dir(recording_id, create=False)
    if not temp_dir.exists():
        record_pipeline_metric(
            stage="live_task_skipped",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "reason": "upload_buffer_missing",
            },
            status="skipped",
            log=logger,
        )
        _record_live_sequence_outcome_metric(
            recording_id=recording_id,
            sequence=sequence,
            outcome="skipped",
            reason="upload_buffer_missing",
        )
        return
    live_dir = temp_dir / "live"
    live_dir.mkdir(parents=True, exist_ok=True)

    state = read_live_state(live_dir)
    source_channel_labels = state[_STATE_SOURCE_CHANNEL_LABELS_KEY]
    session = get_sync_session()
    try:
        manifest_rows = _load_recording_audio_window_manifests(session, recording_id)
        resumed_state = infer_resume_state_from_manifests(manifest_rows)
        if resumed_state and int(resumed_state["next_expected"]) > int(
            state["next_expected"]
        ):
            state["next_expected"] = int(resumed_state["next_expected"])
            state["buffer_abs_start"] = float(resumed_state["buffer_abs_start"])
            record_pipeline_metric(
                stage="live_state_resumed_from_manifest",
                recording_id=recording_id,
                payload={
                    "sequence": sequence,
                    "next_expected": state["next_expected"],
                    "buffer_abs_start": round(float(state["buffer_abs_start"]), 3),
                },
                log=logger,
            )
    finally:
        session.close()

    next_expected = state["next_expected"]
    buffer_abs_start = state["buffer_abs_start"]

    # --- Gating ---
    if sequence < next_expected:
        # Already consumed by an earlier run.
        _record_live_sequence_outcome(
            state,
            sequence=sequence,
            outcome="skipped",
            reason="already_consumed",
        )
        _write_live_state_best_effort(live_dir, state)
        record_pipeline_metric(
            stage="live_sequence_skipped",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "next_expected": next_expected,
                "reason": "already_consumed",
            },
            status="skipped",
            log=logger,
        )
        _record_live_sequence_outcome_metric(
            recording_id=recording_id,
            sequence=sequence,
            outcome="skipped",
            reason="already_consumed",
            extra_payload={"next_expected": next_expected},
        )
        return
    if sequence > next_expected and not (temp_dir / f"{next_expected}.wav").exists():
        # Only a missing head is a gap; lifecycle rejection can retain it.
        _record_live_sequence_outcome(
            state,
            sequence=sequence,
            outcome="deferred",
            reason="waiting_for_gap",
        )
        _write_live_state_best_effort(live_dir, state)
        record_pipeline_metric(
            stage="live_sequence_skipped",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "next_expected": next_expected,
                "reason": "waiting_for_gap",
            },
            status="skipped",
            log=logger,
        )
        _record_live_sequence_outcome_metric(
            recording_id=recording_id,
            sequence=sequence,
            outcome="deferred",
            reason="waiting_for_gap",
            extra_payload={"next_expected": next_expected},
        )
        return

    # Drain capture ingestion; rolling ASR observes only the latest chunk step.
    run = []
    n = next_expected
    while os.path.exists(str(temp_dir / f"{n}.wav")):
        run.append(n)
        n += 1
    if not run:
        # Defensive: the triggering segment should exist; nothing to do.
        _record_live_sequence_outcome(
            state,
            sequence=sequence,
            outcome="skipped",
            reason="triggering_segment_missing",
        )
        _write_live_state_best_effort(live_dir, state)
        record_pipeline_metric(
            stage="live_sequence_skipped",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "next_expected": next_expected,
                "reason": "triggering_segment_missing",
            },
            status="skipped",
            log=logger,
        )
        _record_live_sequence_outcome_metric(
            recording_id=recording_id,
            sequence=sequence,
            outcome="skipped",
            reason="triggering_segment_missing",
            extra_payload={"next_expected": next_expected},
        )
        return

    buffer_path = str(live_dir / _BUFFER_FILENAME)
    context_path = str(live_dir / _CONTEXT_FILENAME)

    live_run = LiveRun(
        recording_id=recording_id,
        sequence=sequence,
        run=run,
        temp_dir=temp_dir,
        live_dir=live_dir,
        buffer_path=buffer_path,
        context_path=context_path,
    )

    labels_ready = False
    combined = None
    label_retention_s = 0.0
    try:
        record_pipeline_metric(
            stage="live_run_started",
            recording_id=recording_id,
            payload={"sequence": sequence, "run": run},
            log=logger,
        )
        # --- Audio buffering: build the combined buffer ---
        combined_channels, combined, combined_len, combined_source_activity = (
            _build_live_combined_buffer(
                temp_dir=temp_dir,
                live_dir=live_dir,
                buffer_path=buffer_path,
                run=run,
            )
        )
        live_run.combined_len = combined_len
        combined_abs_start = buffer_abs_start
        labels_ready = settling_capture
        label_retention_s = 10.0 if settling_capture else 0.0
        logger.info(
            "Live capture channel analysis for recording %s sequence %s run %s: %s",
            recording_id,
            sequence,
            run,
            combined_source_activity,
        )

        live_config = _build_live_config()
        W = int(live_config["context_window_s"] * LIVE_SAMPLE_RATE)
        # Load user-aware overrides once for live speaker matching.
        _resolve_live_engine_config(
            recording_id,
            live_config,
            warn_unusable=not _live_lane_has_consumed(state),
        )
        speech = detect_speech_segments(
            combined,
            min_silence_duration_ms=LIVE_MIN_SILENCE_MS,
            speech_pad_ms=live_config["speech_pad_ms"],
            config=live_config,
        )
        complete, cut_point = classify_speech(
            speech,
            combined_len,
            forced_max_s=float(live_config["forced_max_s"]),
            max_segment_s=float(live_config["max_segment_s"]),
        )
        record_pipeline_metric(
            stage="live_vad_classified",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "run": run,
                "speech_count": len(speech),
                "complete_count": len(complete),
                "combined_len_s": round(combined_len, 3),
                "cut_point_s": round(cut_point, 3),
            },
            log=logger,
        )

        if os.path.exists(context_path):
            prev_context_channels = _read_live_audio_channels(context_path)
        else:
            prev_context_channels = torch.zeros((combined_channels.size(0), 0))
        prev_context_channels = _expand_audio_channels(
            prev_context_channels,
            int(combined_channels.size(0)),
        )
        prev_context = _mix_live_audio_channels(prev_context_channels)

        live_config_hash = build_asr_config_hash(live_config)
        live_model_name = get_transcription_model_name(live_config)
        settling = run_settling_step(
            SettlingStep(
                recording_id,
                run[-1],
                temp_dir,
                live_dir,
                round((combined_abs_start + combined_len) * 1000),
                combined_abs_start,
                speech,
                expected_token,
                source_channel_labels,
            ),
            live_config,
        )
        labels_ready = settling.handled
        if not settling.accepted:
            return
        live_config["_settling_public_changed"] = settling.public_changed
        label_retention_s = settling.window_s if settling.handled else 0.0
        if settling.handled:
            complete, cut_point = [], combined_len
        new_segments = (
            []
            if settling.handled
            else _transcribe_live_regions(
                recording_id=recording_id,
                sequence=sequence,
                complete=complete,
                combined=combined,
                combined_channels=combined_channels,
                combined_abs_start=combined_abs_start,
                prev_context=prev_context,
                context_window_samples=W,
                live_dir=live_dir,
                live_config=live_config,
                source_channel_labels=source_channel_labels,
                state=state,
            )
        )

        # --- Carry over the unconsumed trailing audio and context run-up ---
        with stage_live_audio(
            live_dir=live_dir,
            buffer_path=buffer_path,
            context_path=context_path,
            combined_channels=combined_channels,
            combined_abs_start=combined_abs_start,
            cut_point=cut_point,
            prev_context_channels=prev_context_channels,
            context_window_samples=W,
        ) as carry:
            publication = None
            try:
                publication = _persist_live_run(
                    recording_id=recording_id,
                    sequence=sequence,
                    run=run,
                    new_segments=new_segments,
                    state=state,
                    live_config=live_config,
                    live_config_hash=live_config_hash,
                    live_model_name=live_model_name,
                    expected_token=expected_token,
                )
            finally:
                # Ordinary DB failures retain the prepared tail for existing
                # recovery; stale results leave accepted audio untouched.
                if publication is None or publication.accepted:
                    carry.promote()
                    live_run.carried_abs_start = carry.new_abs_start
            new_abs_start = carry.new_abs_start
        if not publication.accepted:
            return

        # --- Diarisation dispatch (best-effort Meeting Edge refresh) ---
        if publication.dispatch_meeting_edge:
            _dispatch_meeting_edge_refresh_best_effort(recording_id)

        for consumed_sequence in run:
            _record_live_sequence_outcome(
                state,
                sequence=consumed_sequence,
                outcome="consumed",
                reason="live_run_completed",
                run=run,
            )
        state["next_expected"] = run[-1] + 1
        state["buffer_abs_start"] = new_abs_start
        state["buffer_len_s"] = combined_len - cut_point
        write_live_state(live_dir, state)
        labels_ready = True
        _record_live_sequence_outcome_metric(
            recording_id=recording_id,
            sequence=sequence,
            outcome="consumed",
            reason="live_run_completed",
            run=run,
            extra_payload={"next_expected": state["next_expected"]},
        )
        record_pipeline_metric(
            stage="live_run_completed",
            recording_id=recording_id,
            payload={
                "sequence": sequence,
                "run": run,
                "new_segments": len(new_segments),
                "next_expected": state["next_expected"],
                "buffer_abs_start": round(new_abs_start, 3),
            },
            log=logger,
        )

    except Exception as exc:  # noqa: BLE001 -- boundary: live failures are non-fatal
        # Non-fatal: log and move on. Finalize transcribes the whole recording,
        # so the failed run's audio still reaches the final transcript.
        record_live_run_failure(live_run, exc, state)
    finally:
        try:  # labels remain independent of failed/skipped settling ASR
            if (
                labels_ready
                and combined is not None
                and live_speaker_labels_requested()
            ):
                from backend.processing.live_diarization import step as live_labels

                live_labels.run_live_speaker_labels(
                    live_labels.LiveLabelRun(
                        recording_id=recording_id,
                        sequence=run[-1],
                        live_dir=live_dir,
                        audio=combined,
                        audio_start_s=combined_abs_start,
                        next_text_start_s=max(
                            0.0, float(state["buffer_abs_start"]) - label_retention_s
                        ),
                        expected_token=expected_token,
                    )
                )
        except Exception:  # noqa: BLE001 -- boundary: labels are best-effort
            logger.exception("Live speaker labels failed for %s", recording_id)
