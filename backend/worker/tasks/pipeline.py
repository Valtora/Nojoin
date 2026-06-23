from .constants import *


@celery_app.task(
    name="backend.worker.tasks.refresh_meeting_edge_task", base=DatabaseTask, bind=True
)
def refresh_meeting_edge_task(self, recording_id: int):
    session = self.session

    try:
        recording = session.get(Recording, recording_id)
        if not recording:
            return None

        if recording.status not in {
            RecordingStatus.UPLOADING,
            RecordingStatus.QUEUED,
            RecordingStatus.PROCESSING,
        }:
            return None

        transcript = session.exec(
            select(Transcript)
            .where(Transcript.recording_id == recording_id)
            .with_for_update()
        ).first()
        if transcript is None:
            return None

        user_settings = {}
        if recording.user_id:
            user = session.get(User, recording.user_id)
            if user and user.settings:
                user_settings = user.settings

        if not is_meeting_edge_enabled(user_settings):
            if (
                transcript.meeting_edge_status != MEETING_EDGE_STATUS_IDLE
                or transcript.meeting_edge_error_message
            ):
                _set_meeting_edge_state(
                    session,
                    transcript,
                    status=MEETING_EDGE_STATUS_IDLE,
                    error_message=None,
                )
            return None

        segments = [
            dict(segment)
            for segment in build_transcript_segments_for_read(
                session,
                recording_id,
                transcript=transcript,
            )
            if str(segment.get("text", "")).strip()
        ]
        focus_text = transcript.meeting_edge_focus
        user_notes = transcript.user_notes

        if not segments:
            if transcript.meeting_edge_status != MEETING_EDGE_STATUS_IDLE:
                _set_meeting_edge_state(
                    session,
                    transcript,
                    status=MEETING_EDGE_STATUS_IDLE,
                    error_message=None,
                )
            return None

        segment_count = len(segments)
        word_count = _count_meeting_edge_words(segments)
        if not _has_meeting_edge_signal(
            segment_count=segment_count,
            word_count=word_count,
            focus_text=focus_text,
        ):
            if transcript.meeting_edge_status not in {
                MEETING_EDGE_STATUS_IDLE,
                MEETING_EDGE_STATUS_READY,
            }:
                _set_meeting_edge_state(
                    session,
                    transcript,
                    status=MEETING_EDGE_STATUS_IDLE,
                    error_message=None,
                )
            return None

        llm_config = resolve_llm_config(
            session,
            user_settings,
            purpose=LLM_PURPOSE_MEETING_EDGE,
        )
        config_signature = ":".join(
            [
                llm_config.provider,
                llm_config.model or "",
                llm_config.api_url or "",
            ]
        )

        speakers = session.exec(
            select(RecordingSpeaker).where(
                RecordingSpeaker.recording_id == recording_id
            )
        ).all()
        speaker_map = build_recording_speaker_map(speakers)
        recent_transcript = _build_recent_meeting_edge_transcript(segments, speaker_map)
        context_level = get_meeting_edge_context_level(user_settings)
        source_signature = _build_meeting_edge_source_signature(
            recent_transcript=recent_transcript,
            focus_text=focus_text,
            user_notes=user_notes,
            config_signature=config_signature,
            context_level=context_level,
        )

        if not _should_refresh_meeting_edge(
            transcript=transcript,
            source_signature=source_signature,
            current_segment_count=segment_count,
            current_word_count=word_count,
            focus_text=focus_text,
            user_notes=user_notes,
            context_level=context_level,
        ):
            return None

        missing_llm_config = llm_config.missing_configuration_message()
        if missing_llm_config:
            _set_meeting_edge_state(
                session,
                transcript,
                status=MEETING_EDGE_STATUS_ERROR,
                error_message=missing_llm_config,
                source_signature=source_signature,
            )
            return None

        previous_payload = (
            transcript.meeting_edge_payload
            if isinstance(transcript.meeting_edge_payload, dict)
            else {}
        )
        request = MeetingEdgeRequest(
            recent_transcript=recent_transcript,
            rolling_summary=(
                (previous_payload or {}).get("rolling_summary")
                or (previous_payload or {}).get("summary")
            ),
            focus_text=focus_text,
            user_notes=user_notes,
            meeting_context=_resolve_meeting_event_context(session, recording),
            context_level=context_level,
            previous_questions=_read_meeting_edge_payload_items(
                previous_payload, "questions"
            ),
            previous_points=_read_meeting_edge_payload_items(
                previous_payload, "points"
            ),
        )

        _set_meeting_edge_state(
            session,
            transcript,
            status=MEETING_EDGE_STATUS_UPDATING,
            error_message=None,
            source_signature=source_signature,
        )

        llm = _llm_backend_from_config(llm_config)
        result = llm.generate_meeting_edge(
            request,
            timeout=MEETING_EDGE_TIMEOUT_SECONDS,
        )
        payload = serialize_meeting_edge_result(result)
        payload.update(
            {
                "generated_at": utc_now().isoformat(),
                "source_segment_count": segment_count,
                "source_word_count": word_count,
                "source_last_end": float(segments[-1].get("end", 0.0)),
                "focus_hash": _hash_meeting_edge_text(focus_text),
                "user_notes_hash": _hash_meeting_edge_text(user_notes),
                "context_level": context_level,
            }
        )
        previous_context_level_value = previous_payload.get(
            "context_level",
            MEETING_EDGE_CONTEXT_LEVEL_MAX if previous_payload else None,
        )
        try:
            previous_context_level = int(previous_context_level_value)
        except (TypeError, ValueError):
            previous_context_level = (
                MEETING_EDGE_CONTEXT_LEVEL_MAX if previous_payload else None
            )
        payload["concept_history"] = merge_meeting_edge_concept_history(
            previous_payload,
            payload,
            reset_history=previous_context_level is not None
            and previous_context_level > context_level,
        )
        _set_meeting_edge_state(
            session,
            transcript,
            status=MEETING_EDGE_STATUS_READY,
            error_message=None,
            source_signature=source_signature,
            payload=payload,
        )
        return payload
    except Exception as exc:
        logger.error(
            "Meeting Edge refresh failed for recording %s: %s",
            recording_id,
            exc,
            exc_info=True,
        )

        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).first()
        if transcript is not None:
            _set_meeting_edge_state(
                session,
                transcript,
                status=MEETING_EDGE_STATUS_ERROR,
                error_message=str(exc).strip()[:500]
                or "Meeting Edge could not be updated.",
            )
        return None


# ---------------------------------------------------------------------------
# process_recording_task orchestration stages (BE-004)
#
# The canonical finalize pipeline is decomposed into explicit stages with typed
# inputs/outputs and clear failure semantics. The stages run inside the task's
# try/except/finally so Celery retry/ack/error handling, temp-file cleanup, and
# VRAM release stay exactly where they were. Heavy ML inference imports remain
# INSIDE the stage functions (whisper/pyannote/torch/embeddings/etc.) so the API
# process never pays for them at module import time.
# ---------------------------------------------------------------------------


@dataclass
class _PipelineRunContext:
    """Shared handles threaded through the orchestration stages.

    ``task`` is the bound Celery task (used for ``update_state`` progress
    reporting); ``temp_files`` is the running cleanup list the finally block
    drains. These are mutable, deliberately shared references -- the stages
    mutate ``recording``/``temp_files`` in place exactly as the original inline
    code did, preserving every DB write and progress emission.
    """

    task: Task
    session: Any
    recording_id: int
    device_suffix: str
    temp_files: list[str]
    merged_config: dict
    # Set once the VAD stage produces the processed (16k mono) audio; consumed by
    # the speaker-assignment and segmentation-refinement stages.
    processed_audio_path: str | None = None


@dataclass
class _InputAudioResolution:
    """Outcome of resolving the source audio for processing.

    ``audio_path`` is the path to transcribe/diarize. ``finished`` signals an
    early return: repair failed and the task has already persisted ERROR state,
    so the orchestrator returns ``None`` without raising.
    """

    audio_path: str | None
    finished: bool = False


@dataclass
class _VadStageResult:
    """Outcome of the VAD/preprocess stage.

    ``processed_audio_path`` feeds ASR/diarization. ``finished`` signals the
    "no speech detected" short-circuit: an empty transcript has been persisted
    and the recording marked PROCESSED, so the orchestrator returns ``None``.
    """

    processed_audio_path: str | None
    finished: bool = False


def _resolve_input_audio(
    ctx: _PipelineRunContext,
    recording: Recording,
) -> _InputAudioResolution:
    """Resolve, restore, validate/repair, and duration-backfill the source audio.

    Restores from the proxy when the source is missing; repairs invalid audio
    (persisting ERROR and returning early when repair fails); and backfills a
    missing duration best-effort. Raises ``FileNotFoundError`` when no usable
    audio can be obtained -- the orchestrator's handler maps that to ERROR.
    """
    from backend.utils.audio import get_audio_duration

    session = ctx.session
    device_suffix = ctx.device_suffix
    temp_files = ctx.temp_files

    audio_path = recording.audio_path
    if not audio_path or not os.path.exists(audio_path):
        if recording.proxy_path and os.path.exists(recording.proxy_path):
            logger.info(
                "Source audio missing, but proxy exists. Restoring from proxy..."
            )
            from backend.utils.audio import convert_to_wav

            restore_audio_path = audio_path
            if not restore_audio_path:
                base_path, _ = os.path.splitext(recording.proxy_path)
                restore_audio_path = f"{base_path}.restored.wav"
                recording.audio_path = restore_audio_path
            elif not restore_audio_path.lower().endswith(".wav"):
                base_path, _ = os.path.splitext(restore_audio_path)
                restore_audio_path = f"{base_path}.restored.wav"
                recording.audio_path = restore_audio_path

            recording.processing_step = f"Restoring audio from proxy...{device_suffix}"
            session.add(recording)
            session.commit()

            if convert_to_wav(recording.proxy_path, restore_audio_path):
                logger.info("Successfully restored source audio from proxy.")
                audio_path = restore_audio_path
            else:
                raise FileNotFoundError(
                    "Source audio missing and failed to restore from proxy."
                )
        else:
            raise FileNotFoundError(
                f"Audio file not found: {audio_path} and no proxy available."
            )

    from backend.processing.audio_preprocessing import (
        repair_audio_file,
        validate_audio_file,
    )

    try:
        validate_audio_file(audio_path)
    except AudioFormatError as e:
        logger.warning("Invalid audio file detected: %s. Attempting repair...", e)
        repaired_path = repair_audio_file(audio_path)

        if repaired_path:
            logger.info("Using repaired audio file: %s", repaired_path)
            audio_path = repaired_path
            temp_files.append(repaired_path)  # Ensure cleanup
        else:
            logger.error("Audio repair failed for %s", audio_path)
            recording.status = RecordingStatus.ERROR
            recording.processing_step = f"Invalid audio (Repair failed): {str(e)}"
            session.add(recording)
            session.commit()
            return _InputAudioResolution(audio_path=None, finished=True)

    # Fix missing duration if needed
    if not recording.duration_seconds or recording.duration_seconds == 0:
        try:
            duration = get_audio_duration(audio_path)
            recording.duration_seconds = duration
            session.add(recording)
            session.commit()
            session.refresh(recording)
        except Exception as e:  # noqa: BLE001 -- boundary: duration backfill is best-effort
            logger.warning(
                f"Could not determine duration for recording {ctx.recording_id}: {e}"
            )

    return _InputAudioResolution(audio_path=audio_path)


def _run_vad_stage(
    ctx: _PipelineRunContext,
    recording: Recording,
    audio_path: str,
) -> _VadStageResult:
    """Preprocess audio to 16k mono and (when enabled) mute non-speech regions.

    On the "no speech" short-circuit, persists an empty transcript, marks the
    recording PROCESSED, and returns ``finished=True`` so the orchestrator
    returns without running ASR. Otherwise returns the processed audio path.
    """
    from backend.processing.audio_preprocessing import preprocess_audio_for_vad
    from backend.processing.vad import mute_non_speech_segments

    session = ctx.session
    device_suffix = ctx.device_suffix
    temp_files = ctx.temp_files
    recording_id = ctx.recording_id

    enable_vad = ctx.merged_config.get("enable_vad", True)

    if enable_vad:
        ctx.task.update_state(state="PROCESSING", meta={"progress": 30, "stage": "VAD"})
        recording.processing_step = f"Filtering silence and noise...{device_suffix}"
        recording.processing_progress = 30
        session.add(recording)
        session.commit()

        # Preprocess for VAD (resample to 16k mono)
        vad_input_path = preprocess_audio_for_vad(audio_path)
        if not vad_input_path:
            raise RuntimeError("VAD preprocessing failed")
        temp_files.append(vad_input_path)

        # Run VAD (mute silence)
        vad_output_path = vad_input_path.replace("_vad.wav", "_vad_processed.wav")
        vad_success, speech_duration = mute_non_speech_segments(
            vad_input_path, vad_output_path
        )

        if not vad_success:
            raise RuntimeError("VAD execution failed")
        temp_files.append(vad_output_path)

        # Check for silence
        if speech_duration < 1.0:
            logger.warning(
                f"No speech detected in recording {recording_id} (speech duration: {speech_duration}s)"
            )
            recording.status = RecordingStatus.PROCESSED
            recording.client_status = ClientStatus.IDLE
            recording.processing_step = "Completed (No speech detected)"
            recording.processing_completed_at = utc_now()

            # Create empty transcript
            transcript = session.exec(
                select(Transcript).where(Transcript.recording_id == recording.id)
            ).first()
            if not transcript:
                transcript = Transcript(recording_id=recording.id)

            transcript.text = ""  # Empty string to prevent hallucinations
            transcript.segments = []
            transcript.transcript_status = "completed"

            mark_recording_audio_chunks_ready_for_cleanup(
                session,
                recording_id=recording.id,
                upload_status="finalized",
            )
            auto_link_recording(session, recording)
            session.add(transcript)
            session.add(recording)
            session.commit()
            return _VadStageResult(processed_audio_path=None, finished=True)

        # Use WAV for processing to avoid sample count mismatches in Pyannote
        processed_audio_path = vad_output_path
    else:
        logger.info("VAD disabled, skipping silence filtering.")
        # Still need to preprocess to ensure 16k mono wav for Whisper/Pyannote
        vad_input_path = preprocess_audio_for_vad(audio_path)
        if not vad_input_path:
            raise RuntimeError("Audio preprocessing failed")
        temp_files.append(vad_input_path)
        processed_audio_path = vad_input_path

    logger.info(
        f"Using processed audio for transcription/diarization: {processed_audio_path}"
    )
    if not os.path.exists(processed_audio_path):
        raise FileNotFoundError(f"Processed audio file missing: {processed_audio_path}")

    return _VadStageResult(processed_audio_path=processed_audio_path)


def _run_final_asr_stage(
    ctx: _PipelineRunContext,
    recording: Recording,
    processed_audio_path: str,
    engine_override: dict | None,
) -> dict | None:
    """Run the configured transcription engine with ASR-ledger bookkeeping.

    Records a ledger row (start/complete/fail) when the ledger is enabled so the
    manifest/asr_status semantics survive a crash mid-finalize. Re-raises any
    ASR exception after marking the ledger row failed -- the failure flows to the
    orchestrator's error handler unchanged.
    """
    from backend.processing.transcribe import transcribe_audio

    session = ctx.session
    merged_config = ctx.merged_config
    recording_id = ctx.recording_id

    ctx.task.update_state(
        state="PROCESSING", meta={"progress": 50, "stage": "Transcription"}
    )
    recording.processing_step = f"Transcribing audio...{ctx.device_suffix}"
    recording.processing_progress = 50
    session.add(recording)
    session.commit()

    # Apply per-reprocess transcription-engine override, if provided.
    if engine_override:
        merged_config.update(engine_override)
        logger.info("Reprocess: engine override applied: %s", engine_override)

    transcription_result = None

    # Run the configured transcription engine.
    with pipeline_metric_timer(
        stage="final_asr_invocation",
        recording_id=recording_id,
        payload={
            "engine": merged_config.get("transcription_backend"),
            "engine_override": bool(engine_override),
            "input_path": processed_audio_path,
        },
        log=logger,
    ) as metric:
        asr_source_kind = "reprocess" if engine_override else "finalize"
        span_end_ms = int(round(float(recording.duration_seconds or 0.0) * 1000.0))
        if config_manager.get("enable_asr_window_result_ledger", True):
            start_recording_asr_window_result(
                session,
                recording_id=recording.id,
                source_kind=asr_source_kind,
                span_start_ms=0,
                span_end_ms=span_end_ms,
                config=merged_config,
                config_hash=_final_asr_config_hash(merged_config),
            )
        try:
            transcription_result = transcribe_audio(
                processed_audio_path, config=merged_config
            )
        except Exception as exc:
            if config_manager.get("enable_asr_window_result_ledger", True):
                fail_recording_asr_window_result(
                    session,
                    recording_id=recording.id,
                    source_kind=asr_source_kind,
                    span_start_ms=0,
                    span_end_ms=span_end_ms,
                    config=merged_config,
                    config_hash=_final_asr_config_hash(merged_config),
                    error_summary=str(exc).strip()[:500]
                    or "Final ASR invocation failed.",
                    error_payload={"error_type": exc.__class__.__name__},
                )
            raise
        if config_manager.get("enable_asr_window_result_ledger", True):
            if transcription_result is None:
                fail_recording_asr_window_result(
                    session,
                    recording_id=recording.id,
                    source_kind=asr_source_kind,
                    span_start_ms=0,
                    span_end_ms=span_end_ms,
                    config=merged_config,
                    config_hash=_final_asr_config_hash(merged_config),
                    error_summary="Final ASR returned no result.",
                    error_payload={"error_type": "empty_result"},
                )
            else:
                complete_recording_asr_window_result(
                    session,
                    recording_id=recording.id,
                    source_kind=asr_source_kind,
                    span_start_ms=0,
                    span_end_ms=span_end_ms,
                    config=merged_config,
                    config_hash=_final_asr_config_hash(merged_config),
                    result_payload={
                        "segment_count": len(
                            (transcription_result or {}).get("segments", [])
                        ),
                        "text_chars": len(
                            (transcription_result or {}).get("text") or ""
                        ),
                        "engine_override": bool(engine_override),
                    },
                )
        metric["payload"]["segment_count"] = len(
            (transcription_result or {}).get("segments", [])
        )

    return transcription_result


def _run_final_diarization_stage(
    ctx: _PipelineRunContext,
    recording: Recording,
    processed_audio_path: str,
):
    """Run pyannote diarization and the best-effort phantom-speaker filter.

    Returns ``None`` (single-speaker fallback) when diarization is disabled or
    produced no result. The phantom filter is wrapped so a failure there never
    crashes finalize -- the unfiltered diarization is used instead.
    """
    from backend.processing.diarize import diarize_audio

    session = ctx.session
    merged_config = ctx.merged_config
    recording_id = ctx.recording_id

    enable_diarization = merged_config.get("enable_diarization", True)
    diarization_result = None

    if enable_diarization:
        ctx.task.update_state(
            state="PROCESSING", meta={"progress": 70, "stage": "Diarization"}
        )
        recording.processing_step = f"Determining who said what...{ctx.device_suffix}"
        recording.processing_progress = 70
        session.add(recording)
        session.commit()

        # Run Pyannote
        with pipeline_metric_timer(
            stage="final_diarization_invocation",
            recording_id=recording_id,
            payload={
                "input_path": processed_audio_path,
                "enabled": True,
            },
            log=logger,
        ) as metric:
            diarization_result = diarize_audio(
                processed_audio_path, config=merged_config
            )
            metric["payload"]["result_available"] = diarization_result is not None

        if diarization_result is None:
            msg = "Diarization failed (check HF token), falling back to single speaker."
            logger.warning(msg)
            recording.processing_step = msg
            session.add(recording)
            session.commit()
        else:
            # Post-diarization phantom speaker filter
            from backend.processing.phantom_filter import filter_phantom_speakers

            try:
                diarization_result = filter_phantom_speakers(
                    diarization_result, processed_audio_path, config=merged_config
                )
            except Exception as e:  # noqa: BLE001 -- boundary: phantom filter is best-effort
                logger.warning(
                    f"Phantom speaker filter failed, continuing with unfiltered result: {e}"
                )
    else:
        logger.info("Diarization disabled, skipping speaker separation.")

    return diarization_result


def _combine_and_consolidate_segments(
    transcription_result: dict | None,
    diarization_result,
    *,
    enable_diarization: bool,
    recording_id: int,
) -> list[dict]:
    """Merge ASR + diarization into consolidated final segments.

    When no combined result is available (combination skipped or failed) every
    ASR segment is emitted pinned to the ``UNKNOWN`` speaker, preserving any
    ``id``/``words`` payload. This is the load-bearing fallback that keeps a
    transcript even without usable diarization.
    """
    from backend.utils.transcript_utils import (
        combine_transcription_diarization,
        consolidate_diarized_transcript,
    )

    # Combine Transcription and Diarization
    combined_segments = []
    if transcription_result:
        # Only attempt combination if we have both results
        if diarization_result:
            combined_segments = combine_transcription_diarization(
                transcription_result, diarization_result
            )
        else:
            logger.info("Diarization result missing or disabled. Skipping combination.")

    logger.info(
        f"Combined segments count: {len(combined_segments) if combined_segments else 0}"
    )

    if not combined_segments:
        # Fallback if combination fails or was skipped
        if enable_diarization and diarization_result:
            logger.warning(
                "Combination failed despite having diarization result. Using raw transcription segments with UNKNOWN speaker."
            )
        else:
            logger.info(
                "Using raw transcription segments (Diarization disabled or failed)."
            )

        # Check if transcription_result is None before accessing
        if transcription_result and "segments" in transcription_result:
            combined_segments = []
            for seg in transcription_result.get("segments", []):
                fallback_segment = {
                    "start": seg["start"],
                    "end": seg["end"],
                    "speaker": "UNKNOWN",
                    "text": seg["text"].strip(),
                }
                if seg.get("id"):
                    fallback_segment["id"] = seg["id"]
                if seg.get("words"):
                    fallback_segment["words"] = seg["words"]
                combined_segments.append(fallback_segment)
        else:
            logger.error(
                "Transcription result is None or missing segments during fallback."
            )
            combined_segments = []

    # Consolidate segments
    final_segments = consolidate_diarized_transcript(combined_segments)
    record_pipeline_metric(
        stage="final_segments_built",
        recording_id=recording_id,
        payload={"segment_count": len(final_segments)},
        log=logger,
    )
    logger.info("Final segments after consolidation: %s", len(final_segments))
    return final_segments


def _persist_final_transcript(
    ctx: _PipelineRunContext,
    recording: Recording,
    final_segments: list[dict],
    transcription_result: dict | None,
) -> Transcript:
    """Create or update the transcript row with the consolidated segments.

    Handles a ``None`` transcription result by persisting empty text, and resets
    a stale ``notes_status == "error"`` back to ``pending`` so notes regenerate.
    """
    session = ctx.session

    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording.id)
    ).first()

    # Create or Update Transcript Record
    # Handle case where transcription_result is None (e.g. due to error)
    full_text = transcription_result.get("text", "") if transcription_result else ""

    if transcript:
        transcript.text = full_text
        transcript.segments = final_segments
        transcript.transcript_status = "completed"
        transcript.error_message = None
        if transcript.notes_status == "error":
            transcript.notes_status = "pending"
        session.add(transcript)
    else:
        transcript = Transcript(
            recording_id=recording.id,
            text=full_text,
            segments=final_segments,
            transcript_status="completed",
        )
        session.add(transcript)

    session.commit()
    return transcript


def _assign_and_identify_speakers(
    ctx: _PipelineRunContext,
    recording: Recording,
    final_segments: list[dict],
    diarization_result,
) -> None:
    """Resolve diarization labels to speakers, persisting RecordingSpeaker rows.

    Preserves two load-bearing invariants:

    * Manual-edit authority -- a speaker carrying a ``local_name`` (or a merge
      target) is treated as identified and is never re-matched against global
      voiceprints.
    * Stable-id alignment -- when a resolved name was already assigned to an
      earlier label, this label is auto-merged into the first one and the
      in-memory ``final_segments`` (and any ``overlapping_speakers``) are
      rewritten to the canonical target label so the transcript stays coherent.

    Heavy embedding imports stay local to this stage.
    """
    from backend.processing.embedding import (
        AUTO_UPDATE_THRESHOLD,
        find_matching_global_speaker,
        merge_embeddings,
    )
    from backend.processing.embedding_core import extract_embeddings

    session = ctx.session
    merged_config = ctx.merged_config

    # Save Speakers & Embeddings
    # Processes speakers in order of appearance to assign "Speaker 1", "Speaker 2", etc.
    ordered_speakers = _collect_ordered_final_speaker_labels(final_segments)

    logger.info(
        f"Extracted {len(ordered_speakers)} unique speakers from segments: {ordered_speakers}"
    )

    # Extract embeddings for all speakers in the diarization result (if enabled)
    # Voiceprint extraction can be disabled to speed up processing
    enable_auto_voiceprints = merged_config.get("enable_auto_voiceprints", True)
    speaker_embeddings = {}

    # label_map_from_final_to_live is reserved for live-reuse alignment; it stays
    # empty in the canonical finalize path but its remap is preserved verbatim.
    label_map_from_final_to_live: dict = {}

    if enable_auto_voiceprints and diarization_result:
        ctx.task.update_state(
            state="PROCESSING", meta={"progress": 90, "stage": "Voiceprints"}
        )
        recording.processing_step = f"Learning voiceprints...{ctx.device_suffix}"
        recording.processing_progress = 90
        session.add(recording)
        session.commit()
        logger.info("Extracting speaker voiceprints (enable_auto_voiceprints=True)")
        speaker_embeddings = extract_embeddings(
            ctx.processed_audio_path,
            diarization_result,
            device_str=merged_config.get("processing_device", "cpu"),
            config=merged_config,
        )
        if label_map_from_final_to_live:
            speaker_embeddings = {
                label_map_from_final_to_live.get(label, label): embedding
                for label, embedding in speaker_embeddings.items()
            }
    elif not enable_auto_voiceprints:
        logger.info("Skipping voiceprint extraction (enable_auto_voiceprints=False)")

    # Map local labels (SPEAKER_00) to resolved names (John Doe or Speaker 1)
    label_map = {}
    speaker_counter = 1

    # Track which names have been assigned to which speaker ID/Label to detect duplicates
    # Format: name -> {'id': recording_speaker_id, 'label': diarization_label}
    resolved_names_map = {}

    for label in ordered_speakers:
        # Check if speaker already exists for this recording (idempotency)
        existing_speaker = session.exec(
            select(RecordingSpeaker)
            .where(RecordingSpeaker.recording_id == recording.id)
            .where(RecordingSpeaker.diarization_label == label)
        ).first()

        embedding = speaker_embeddings.get(label)
        resolved_name = label  # Default fallback
        global_speaker_id = None
        is_identified = False

        # --- LOGIC UPDATE: Check for Manual Names & Merges ---
        if existing_speaker:
            # 1. Check if this speaker was merged into another
            if existing_speaker.merged_into_id:
                logger.info("Speaker %s is merged. Resolving target...", label)
                current_spk = existing_speaker
                visited_ids = {current_spk.id}

                # Follow the merge chain (prevent infinite loops)
                while current_spk.merged_into_id:
                    next_spk = session.get(RecordingSpeaker, current_spk.merged_into_id)
                    if not next_spk:
                        logger.warning(
                            f"Merge chain broken for speaker {label} at ID {current_spk.merged_into_id}"
                        )
                        break
                    if next_spk.id in visited_ids:
                        logger.warning(f"Circular merge detected for speaker {label}")
                        break
                    visited_ids.add(next_spk.id)
                    current_spk = next_spk

                # Use the target speaker's name
                resolved_name = (
                    current_spk.name
                    or current_spk.local_name
                    or current_spk.diarization_label
                )
                logger.info("Resolved %s (Merged) -> %s", label, resolved_name)
                if current_spk.global_speaker_id:
                    global_speaker_id = current_spk.global_speaker_id
                    is_identified = True  # Don't re-identify
                else:
                    # It's a local merge, so we trust the local name
                    is_identified = True

            # 2. Check for manual rename (if not merged)
            elif existing_speaker.local_name:
                resolved_name = existing_speaker.local_name
                logger.info(
                    f"Preserving manual name for {label}: {existing_speaker.local_name}"
                )
                is_identified = True  # Skip inference

                if existing_speaker.global_speaker_id:
                    global_speaker_id = existing_speaker.global_speaker_id

        # Try to identify speaker using embedding (ONLY if not manually named/merged)
        if not is_identified and embedding:
            # Fetch all global speakers with embeddings belonging to this user
            # Filter out any potential placeholder names from the global list to prevent bad linking
            all_global_speakers = session.exec(
                select(GlobalSpeaker)
                .where(GlobalSpeaker.embedding != None)
                .where(GlobalSpeaker.user_id == recording.user_id)
            ).all()

            import re

            placeholder_pattern = re.compile(
                r"^(SPEAKER_\d+|Speaker \d+|Unknown)$", re.IGNORECASE
            )

            global_speakers = [
                gs
                for gs in all_global_speakers
                if not placeholder_pattern.match(gs.name)
                and gs.embedding
                and len(gs.embedding) > 0
                and not any(x is None for x in gs.embedding)
            ]

            # Use centralized matching logic with 0.75 threshold and margin of victory
            best_match, best_score = find_matching_global_speaker(
                embedding, global_speakers, threshold=0.75, margin=0.05
            )

            if best_match:
                logger.info(
                    f"Identified {label} as {best_match.name} (Score: {best_score:.2f})"
                )
                resolved_name = best_match.name
                global_speaker_id = best_match.id
                is_identified = True

                # Active Learning: only update the global embedding when the
                # match confidence is high enough to avoid polluting it with
                # borderline or false-positive identifications.
                if (
                    not best_match.is_voiceprint_locked
                    and best_score >= AUTO_UPDATE_THRESHOLD
                ):
                    try:
                        new_emb = merge_embeddings(best_match.embedding, embedding)
                        best_match.embedding = new_emb
                        session.add(best_match)
                    except Exception as e:  # noqa: BLE001 -- boundary: embedding update is best-effort
                        logger.warning(
                            f"Failed to update embedding for {best_match.name}: {e}"
                        )
                elif not best_match.is_voiceprint_locked:
                    logger.info(
                        f"Skipping auto-update for {best_match.name} "
                        f"(score {best_score:.2f} < auto-update threshold {AUTO_UPDATE_THRESHOLD})"
                    )
            else:
                logger.info(
                    f"No match found for {label} (Best score: {best_score:.2f})."
                )

        # If not identified as a global speaker, assign a friendly sequential name
        if not is_identified:
            resolved_name = f"Speaker {speaker_counter}"
            speaker_counter += 1

        # Auto-promotion logic removed. Speakers must be manually promoted.

        # Auto-merge duplicate name detection: if this resolved name was already
        # assigned to a previous speaker in this loop, merge into the existing one.
        if resolved_name and resolved_name in resolved_names_map:
            target_info = resolved_names_map[resolved_name]
            target_label = target_info["label"]
            target_id = target_info["id"]

            if target_label != label:
                logger.info(
                    f"Auto-Merge: '{resolved_name}' already assigned to {target_label}. Merging {label} into {target_label}."
                )

                if existing_speaker:
                    existing_speaker.merged_into_id = target_id
                    existing_speaker.name = resolved_name  # Keep consistent name
                    existing_speaker.local_name = None
                    session.add(existing_speaker)
                    session.flush()  # Ensure it's saved
                else:
                    # Create the record but immediately merge it
                    rec_speaker = RecordingSpeaker(
                        recording_id=recording.id,
                        diarization_label=label,
                        name=resolved_name,
                        embedding=embedding,
                        global_speaker_id=global_speaker_id,
                        merged_into_id=target_id,
                    )
                    session.add(rec_speaker)
                    session.flush()

                # rewrite segments in memory to point to the target label
                # This ensures the transcript assumes they are the same speaker
                for seg in final_segments:
                    if seg["speaker"] == label:
                        seg["speaker"] = target_label

                    if "overlapping_speakers" in seg:
                        for idx, ov_spk in enumerate(seg["overlapping_speakers"]):
                            if ov_spk == label:
                                seg["overlapping_speakers"][idx] = target_label

                # No addition to resolved_names_map needed; the canonical entry already exists.
                label_map[label] = resolved_name
                continue

        label_map[label] = resolved_name
        logger.info("Mapped %s -> %s", label, resolved_name)

        current_speaker_id = None
        if existing_speaker:
            if embedding is not None:
                existing_speaker.embedding = embedding
            elif existing_speaker.embedding:
                logger.info(
                    "Preserving existing voiceprint for %s because final diarization produced no embedding.",
                    label,
                )
            existing_speaker.name = resolved_name
            if (
                global_speaker_id is not None
                or existing_speaker.global_speaker_id is None
            ):
                existing_speaker.global_speaker_id = global_speaker_id
            session.add(existing_speaker)
            session.flush()
            current_speaker_id = existing_speaker.id
        else:
            rec_speaker = RecordingSpeaker(
                recording_id=recording.id,
                diarization_label=label,
                name=resolved_name,
                embedding=embedding,
                global_speaker_id=global_speaker_id,
            )
            session.add(rec_speaker)
            session.flush()
            current_speaker_id = rec_speaker.id

        # Register this name as taken
        if resolved_name and current_speaker_id:
            resolved_names_map[resolved_name] = {
                "id": current_speaker_id,
                "label": label,
            }

    # --- Embedding-based speaker merge pass ---
    # Catches over-clustered speakers that the name-based auto-merge
    # above cannot detect (e.g. two clusters both named "Speaker N"
    # before global identification, or same global speaker split into
    # two RecordingSpeaker rows).
    try:
        from backend.processing.speaker_merge import merge_duplicate_speakers

        merge_pairs = merge_duplicate_speakers(
            session,
            recording_id=recording.id,
            segments=final_segments,
        )
        if merge_pairs:
            logger.info(
                "[SpeakerMerge] Merged %d duplicate speaker(s) in recording %d",
                len(merge_pairs),
                ctx.recording_id,
            )
    except Exception as e:  # noqa: BLE001 -- boundary: merge pass is best-effort
        logger.warning("[SpeakerMerge] Merge pass failed, continuing: %s", e)


def _finalize_transcript_and_notes(
    ctx: _PipelineRunContext,
    recording: Recording,
    transcript: Transcript,
    final_segments: list[dict],
    llm_config: ResolvedLLMConfig,
    transcription_result: dict | None,
    reused_live_transcript_segments: Sequence[dict],
) -> None:
    """Persist final segments, run canonical writes + segmentation refinement,
    and trigger the automatic meeting-intelligence (notes/title) stage.

    Canonical writes and the frame-level segmentation refinement safety net are
    gated on ``enable_canonical_transcript_writes``; the refinement pass is
    wrapped best-effort so a failure never aborts finalize.
    """
    session = ctx.session
    device_suffix = ctx.device_suffix
    merged_config = ctx.merged_config
    recording_id = ctx.recording_id

    # Keep the diarization_label in the segments to maintain the link to RecordingSpeaker
    # The frontend will resolve the display name using the speaker map
    updated_segments = []
    for seg in final_segments:
        updated_segments.append(seg)

    ctx.task.update_state(
        state="PROCESSING", meta={"progress": 92, "stage": "Finalizing"}
    )
    recording.processing_step = f"Finalizing transcript structure...{device_suffix}"
    recording.processing_progress = 92
    session.add(recording)
    session.commit()

    # Log final speaker distribution in updated segments
    final_speaker_counts = {}
    for seg in updated_segments:
        spk = seg["speaker"]
        final_speaker_counts[spk] = final_speaker_counts.get(spk, 0) + 1
        for ov_spk in seg.get("overlapping_speakers", []):
            final_speaker_counts[ov_spk] = final_speaker_counts.get(ov_spk, 0) + 1
    logger.info("Final transcript speaker distribution: %s", final_speaker_counts)

    transcript.segments = updated_segments
    session.add(transcript)
    if config_manager.get("enable_canonical_transcript_writes", True):
        finalize_utterances_from_segments(
            session,
            recording_id=recording.id,
            segments=[dict(segment) for segment in updated_segments],
            reused_live_asr=bool(reused_live_transcript_segments),
            trigger_source="worker",
        )
        updated_segments = refresh_transcript_projection_from_canonical(
            session,
            recording.id,
        )

        # Phase F4: frame-level segmentation safety net for utterances
        # that span a speaker change but slipped through rolling
        # diarization's coarser turn boundaries.
        try:
            ctx.task.update_state(
                state="PROCESSING", meta={"progress": 94, "stage": "Refining"}
            )
            recording.processing_step = f"Refining speaker boundaries...{device_suffix}"
            recording.processing_progress = 94
            session.add(recording)
            session.commit()
            with pipeline_metric_timer(
                stage="segmentation_refinement",
                recording_id=recording_id,
                payload={"input_path": ctx.processed_audio_path},
                log=logger,
            ) as seg_metric:
                seg_summary = refine_recording_utterances_via_segmentation(
                    session,
                    recording_id=recording.id,
                    audio_path=ctx.processed_audio_path,
                    device_str=str(merged_config.get("processing_device", "auto")),
                    hf_token=config_manager.get("hf_token"),
                    source="finalize_segmentation_refinement",
                )
                seg_metric["payload"].update(seg_summary)
            if (seg_summary or {}).get("refined_utterance_count", 0) > 0:
                updated_segments = refresh_transcript_projection_from_canonical(
                    session,
                    recording.id,
                )
        except Exception as seg_exc:  # noqa: BLE001 -- boundary: refinement pass is best-effort
            logger.warning(
                "Segmentation refinement pass failed for recording %s: %s",
                recording.id,
                seg_exc,
                exc_info=True,
            )

    recording_speakers = session.exec(
        select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording.id)
    ).all()
    unresolved_speakers = get_speakers_eligible_for_llm_renaming(recording_speakers)
    speaker_map = build_recording_speaker_map(recording_speakers)
    transcript_text = _build_automatic_meeting_intelligence_transcript(
        updated_segments,
        speaker_map,
        unresolved_speakers,
    )

    _run_automatic_meeting_intelligence_stage(
        session=session,
        task=ctx.task,
        recording=recording,
        transcript=transcript,
        speakers=recording_speakers,
        transcript_text=transcript_text,
        unresolved_speakers=unresolved_speakers,
        llm_config=llm_config,
        prefer_short_titles=merged_config.get("prefer_short_titles", True),
        device_suffix=device_suffix,
        detected_transcription_language=(transcription_result or {}).get("language"),
    )


def _release_pipeline_vram() -> None:
    """Best-effort release of cached ML models / VRAM after the task.

    Heavy ML imports stay inside this helper so importing the worker module
    never loads torch/whisper/pyannote. Wrapped so cleanup never crashes the
    task's finally block.
    """
    import torch

    try:
        logger.info("Releasing VRAM (keep_models_loaded=False)...")

        # 1. Whisper
        from backend.processing.transcribe import release_model_cache

        release_model_cache()

        # 2. Pyannote
        from backend.processing.diarize import release_pipeline_cache

        release_pipeline_cache()

        # 3. Speaker Embeddings
        from backend.processing.embedding_core import release_embedding_model_cache

        release_embedding_model_cache()

        # 4. Segmentation Refinement
        from backend.processing.segmentation_refinement import (
            release_segmentation_model_cache,
        )

        release_segmentation_model_cache()

        # 5. Text Embeddings
        from backend.processing.text_embedding import release_embedding_model

        release_embedding_model()

        # 6. Garbage Collection
        import gc

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        logger.info("VRAM released successfully.")
    except Exception as e:  # noqa: BLE001 -- boundary: VRAM release is best-effort
        logger.error("Error releasing VRAM: %s", e)


@celery_app.task(
    name="backend.worker.tasks.process_recording_task",
    base=DatabaseTask,
    bind=True,
    autoretry_for=(
        ConnectionError,
        urllib.error.URLError,
        requests.exceptions.RequestException,
    ),
    retry_backoff=True,
    max_retries=3,
)
def process_recording_task(
    self,
    recording_id: int,
    force_title_regeneration: bool = False,
    engine_override: dict | None = None,
):
    """
    Full processing pipeline: VAD -> Transcribe -> Diarize -> Save

    The body is a slim orchestrator: it sets up the run context, then drives the
    explicit stages (resolve audio -> VAD -> ASR -> diarization -> combine/persist
    -> speaker assignment -> finalize/notes). The surrounding try/except/finally
    owns Celery retry/error semantics, temp-file cleanup, and VRAM release, all
    unchanged from the original inline implementation.
    """
    config_manager.reload()

    start_time = time.time()
    session = self.session
    temp_files: list[str] = []
    catch_up_run: ProcessingRun | None = None
    catch_up_processed_window_ids: set[int] = set()
    catch_up_failed_window_ids: set[int] = set()

    recording = session.get(Recording, recording_id)
    if not recording:
        logger.error("Recording %s not found.", recording_id)
        return

    # Check if cancelled
    if recording.status == RecordingStatus.CANCELLED:
        logger.info("Recording %s was cancelled. Aborting task.", recording_id)
        return

    user_settings = {}
    if recording.user_id:
        user = session.get(User, recording.user_id)
        if user and user.settings:
            user_settings = user.settings
            logger.info(
                f"Loaded settings for user {user.username}: {list(user_settings.keys())}"
            )

    llm_config = resolve_llm_config(session, user_settings)
    merged_config = llm_config.merged_config
    live_segments_for_reuse = []
    if engine_override is None:
        if config_manager.get("enable_canonical_transcript_writes", True):
            live_segments_for_reuse = build_reusable_live_segments(
                session, recording.id
            )
        if not live_segments_for_reuse:
            initial_transcript = session.exec(
                select(Transcript).where(Transcript.recording_id == recording.id)
            ).first()
            if initial_transcript and initial_transcript.segments:
                live_segments_for_reuse = [
                    dict(segment)
                    for segment in initial_transcript.segments
                    if segment.get("segment_source") in {"live", "catch_up"}
                    or segment.get("provisional") is True
                ]

    # Platform/Device detection for UX
    import torch

    device_type = "cpu"
    if config_manager.get("use_gpu", True) and torch.cuda.is_available():
        device_type = "cuda"

    # "Gentle" warning suffix
    device_suffix = " (GPU)" if device_type == "cuda" else " (CPU, may take a while)"

    ctx = _PipelineRunContext(
        task=self,
        session=session,
        recording_id=recording_id,
        device_suffix=device_suffix,
        temp_files=temp_files,
        merged_config=merged_config,
    )

    from backend.processing.audio_preprocessing import cleanup_temp_file

    reused_live_transcript_segments: list = []

    try:
        recording.status = RecordingStatus.PROCESSING
        recording.processing_progress = 20
        if (
            recording.processing_started_at is None
            or recording.processing_completed_at is not None
        ):
            recording.processing_started_at = utc_now()
        recording.processing_completed_at = None
        session.add(recording)
        session.commit()
        session.refresh(recording)

        # --- Stage: resolve/restore/validate source audio ---
        audio_resolution = _resolve_input_audio(ctx, recording)
        if audio_resolution.finished:
            return
        audio_path = audio_resolution.audio_path

        # --- Stage: VAD / preprocess ---
        vad_result = _run_vad_stage(ctx, recording, audio_path)
        if vad_result.finished:
            return
        processed_audio_path = vad_result.processed_audio_path
        # Cache for the speaker-assignment / refinement stages.
        ctx.processed_audio_path = processed_audio_path

        # --- Stage: transcription (ASR) ---
        transcription_result = _run_final_asr_stage(
            ctx, recording, processed_audio_path, engine_override
        )

        # --- Stage: diarization ---
        diarization_result = _run_final_diarization_stage(
            ctx, recording, processed_audio_path
        )
        enable_diarization = merged_config.get("enable_diarization", True)

        # --- Stage: merge & save ---
        self.update_state(state="PROCESSING", meta={"progress": 85, "stage": "Saving"})
        recording.processing_step = f"Saving transcript...{device_suffix}"
        recording.processing_progress = 85
        session.add(recording)
        session.commit()

        final_segments = _combine_and_consolidate_segments(
            transcription_result,
            diarization_result,
            enable_diarization=enable_diarization,
            recording_id=recording_id,
        )

        transcript = _persist_final_transcript(
            ctx, recording, final_segments, transcription_result
        )

        if (
            catch_up_run is not None
            or catch_up_processed_window_ids
            or catch_up_failed_window_ids
        ):
            if catch_up_run is not None:
                if catch_up_failed_window_ids:
                    catch_up_run.status = ProcessingRunStatus.FAILED
                    catch_up_run.error_summary = f"{len(catch_up_failed_window_ids)} catch-up diarization window(s) failed"
                else:
                    catch_up_run.status = ProcessingRunStatus.COMPLETED
                    catch_up_run.error_summary = None
                catch_up_run.completed_at = utc_now()
                session.add(catch_up_run)
            session.commit()
        # update_recording_status(session, recording.id) # Removed to prevent premature status update (flash)

        # --- Stage: speaker assignment / identification ---
        _assign_and_identify_speakers(
            ctx, recording, final_segments, diarization_result
        )

        # --- Stage: finalize transcript + notes/title ---
        _finalize_transcript_and_notes(
            ctx,
            recording,
            transcript,
            final_segments,
            llm_config,
            transcription_result,
            reused_live_transcript_segments,
        )

        # Update Recording Status
        mark_recording_audio_chunks_ready_for_cleanup(
            session,
            recording_id=recording.id,
            upload_status="finalized",
        )
        recording.client_status = ClientStatus.IDLE
        recording.processing_step = "Completed"
        recording.processing_progress = 100
        recording.processing_completed_at = utc_now()
        auto_link_recording(session, recording)
        session.add(recording)
        session.commit()
        update_recording_status(session, recording.id)

        elapsed_time = time.time() - float(start_time)
        record_pipeline_metric(
            stage="final_processing_completed",
            recording_id=recording_id,
            payload={"status": "success"},
            elapsed_ms=elapsed_time * 1000.0,
            log=logger,
        )
        logger.info(
            f"Recording: [{recording_id}] processing succeeded in {elapsed_time:.2f} seconds"
        )

        # Trigger Transcript Indexing for RAG
        # Triggers transcript indexing after all data is committed.
        from backend.worker.tasks import index_transcript_task

        index_transcript_task.delay(recording_id)

        return {"status": "success", "recording_id": recording_id}

    except AudioProcessingError as e:
        record_pipeline_metric(
            stage="final_processing_failed",
            recording_id=recording_id,
            payload={"error": str(e), "error_type": "AudioProcessingError"},
            status="error",
            log=logger,
        )
        logger.error(
            "Audio processing error for %s: %s", recording_id, e, exc_info=True
        )
        if hasattr(session, "rollback"):
            try:
                session.rollback()
            except Exception as rollback_exc:  # noqa: BLE001 -- boundary: rollback is best-effort
                logger.warning(
                    "Failed to rollback session after audio processing error for %s: %s",
                    recording_id,
                    rollback_exc,
                )
        recording = session.get(Recording, recording_id)
        if recording:
            if catch_up_run is not None:
                catch_up_run.status = ProcessingRunStatus.FAILED
                catch_up_run.error_summary = str(e)
                catch_up_run.completed_at = utc_now()
                session.add(catch_up_run)
            recording.status = RecordingStatus.ERROR
            recording.processing_step = f"Error: {str(e)}"
            recording.processing_completed_at = None
            session.add(recording)
            session.commit()
            update_recording_status(session, recording.id)

    except Exception as e:
        record_pipeline_metric(
            stage="final_processing_failed",
            recording_id=recording_id,
            payload={"error": str(e), "error_type": type(e).__name__},
            status="error",
            log=logger,
        )
        logger.error("Processing failed for %s: %s", recording_id, e, exc_info=True)
        if hasattr(session, "rollback"):
            try:
                session.rollback()
            except Exception as rollback_exc:  # noqa: BLE001 -- boundary: rollback is best-effort
                logger.warning(
                    "Failed to rollback session after processing error for %s: %s",
                    recording_id,
                    rollback_exc,
                )
        recording = session.get(Recording, recording_id)
        if recording:
            if catch_up_run is not None:
                catch_up_run.status = ProcessingRunStatus.FAILED
                catch_up_run.error_summary = str(e)
                catch_up_run.completed_at = utc_now()
                session.add(catch_up_run)
            recording.status = RecordingStatus.ERROR
            recording.processing_step = f"System Error: {str(e)}"
            recording.processing_completed_at = None
            session.add(recording)
            session.commit()
            update_recording_status(session, recording.id)

    finally:
        # Robust cleanup of all temporary files
        for temp_file in temp_files:
            cleanup_temp_file(temp_file)

        # --- VRAM Management ---
        # Explicitly release models if configured to do so (default behavior for shared hosts)
        keep_loaded = config_manager.get("keep_models_loaded", False)

        if not keep_loaded:
            _release_pipeline_vram()


@worker_ready.connect
def check_queued_recordings(sender, **kwargs):
    """
    On worker startup, check for any recordings that are stuck in QUEUED state
    and re-queue them.
    """
    logger.info("Checking for pending QUEUED recordings...")
    session = get_sync_session()
    try:
        statement = select(Recording).where(Recording.status == RecordingStatus.QUEUED)
        recordings = session.exec(statement).all()

        if not recordings:
            logger.info("No pending recordings found.")
            return

        logger.info("Found %s pending recordings. Re-queueing...", len(recordings))

        for recording in recordings:
            logger.info("Re-queueing recording %s: %s", recording.id, recording.name)
            process_recording_task.delay(recording.id)  # type: ignore

    except Exception as e:
        logger.error("Failed to check pending recordings: %s", e, exc_info=True)
    finally:
        session.close()


def _final_asr_config_hash(merged_config: dict) -> str:
    transcription_backend = str(merged_config.get("transcription_backend", "whisper"))
    effective_language = resolve_transcription_language_code(
        merged_config,
        transcription_backend,
    )
    return hashlib.sha256(
        "|".join(
            [
                transcription_backend,
                str(merged_config.get("whisper_model_size", "turbo")),
                str(merged_config.get("parakeet_model", "parakeet-tdt-0.6b-v3")),
                str(merged_config.get("canary_model", "nemo-canary-1b-v2")),
                str(merged_config.get("processing_device", "auto")),
                str(bool(merged_config.get("use_gpu", True))),
                str(effective_language or "auto"),
            ]
        ).encode("utf-8")
    ).hexdigest()


def _paths_point_to_same_media_impl(path_a: str | None, path_b: str | None) -> bool:
    if not path_a or not path_b:
        return False

    try:
        if os.path.exists(path_a) and os.path.exists(path_b):
            return os.path.samefile(path_a, path_b)
    except OSError:
        pass

    return os.path.normcase(os.path.abspath(path_a)) == os.path.normcase(
        os.path.abspath(path_b)
    )


def _can_delete_source_audio(recording: Recording) -> bool:
    if not recording.audio_path or not recording.proxy_path:
        return False
    if not os.path.exists(recording.audio_path) or not os.path.exists(
        recording.proxy_path
    ):
        return False

    return not _paths_point_to_same_media(recording.audio_path, recording.proxy_path)


def _recording_uses_browser_capture_impl(session, recording_id: int) -> bool:
    try:
        statement = (
            select(RecordingAudioChunk.id)
            .where(RecordingAudioChunk.recording_id == recording_id)
            .where(RecordingAudioChunk.source_kind == "browser")
            .limit(1)
        )
        return session.exec(statement).first() is not None
    except Exception:  # noqa: BLE001
        return False


def _llm_backend_from_config_impl(llm_config: ResolvedLLMConfig):
    from backend.processing.llm_services import get_llm_backend_with_secondary

    return get_llm_backend_with_secondary(llm_config)


def _count_meeting_edge_words(segments: Sequence[dict]) -> int:
    total = 0
    for segment in segments:
        total += len(str(segment.get("text", "")).split())
    return total


def _has_meeting_edge_signal_impl(
    *,
    segment_count: int,
    word_count: int,
    focus_text: str | None,
) -> bool:
    min_segments = (
        MEETING_EDGE_FOCUSED_MIN_SEGMENTS if focus_text else MEETING_EDGE_MIN_SEGMENTS
    )
    min_words = MEETING_EDGE_FOCUSED_MIN_WORDS if focus_text else MEETING_EDGE_MIN_WORDS
    return word_count >= min_words or (
        segment_count >= min_segments and word_count >= max(18, min_words // 2)
    )


def _build_recent_meeting_edge_transcript(
    segments: Sequence[dict],
    speaker_map: dict[str, str],
) -> str:
    lines: list[str] = []
    total_chars = 0

    for segment in reversed(list(segments)[-MEETING_EDGE_RECENT_SEGMENTS:]):
        rendered = format_segments_for_llm([segment], speaker_map)
        if not rendered:
            continue
        rendered_length = len(rendered) + 1
        if lines and total_chars + rendered_length > MEETING_EDGE_MAX_TRANSCRIPT_CHARS:
            break
        lines.append(rendered)
        total_chars += rendered_length

    return "\n".join(reversed(lines)).strip()


def _hash_meeting_edge_text(value: str | None) -> str:
    cleaned = (value or "").strip()
    return hashlib.sha1(cleaned.encode("utf-8")).hexdigest()


def _build_meeting_edge_source_signature(
    *,
    recent_transcript: str,
    focus_text: str | None,
    user_notes: str | None,
    config_signature: str,
    context_level: int | None = None,
) -> str:
    parts = [
        recent_transcript.strip(),
        (focus_text or "").strip(),
        (user_notes or "").strip(),
        config_signature,
    ]
    if context_level is not None:
        parts.append(str(context_level))
    payload = "\n||\n".join(parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _read_meeting_edge_payload_items(payload: dict | None, key: str) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        return ()
    value = payload.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _parse_meeting_edge_generated_at(payload: dict | None) -> datetime | None:
    if not isinstance(payload, dict):
        return None

    raw_value = payload.get("generated_at")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None

    try:
        return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        return None


def _should_refresh_meeting_edge_impl(
    *,
    transcript: Transcript,
    source_signature: str,
    current_segment_count: int,
    current_word_count: int,
    focus_text: str | None,
    user_notes: str | None,
    context_level: int | None = None,
) -> bool:
    if (
        transcript.meeting_edge_source_signature == source_signature
        and transcript.meeting_edge_status
        in {
            MEETING_EDGE_STATUS_READY,
            MEETING_EDGE_STATUS_UPDATING,
            MEETING_EDGE_STATUS_ERROR,
        }
    ):
        return False

    previous_payload = (
        transcript.meeting_edge_payload
        if isinstance(transcript.meeting_edge_payload, dict)
        else {}
    )
    previous_generated_at = _parse_meeting_edge_generated_at(previous_payload)
    previous_segment_count = int(previous_payload.get("source_segment_count") or 0)
    previous_word_count = int(previous_payload.get("source_word_count") or 0)
    focus_changed = previous_payload.get("focus_hash") != _hash_meeting_edge_text(
        focus_text
    )
    user_notes_changed = previous_payload.get(
        "user_notes_hash"
    ) != _hash_meeting_edge_text(user_notes)
    context_level_changed = (
        context_level is not None
        and previous_payload.get("context_level") is not None
        and previous_payload.get("context_level") != context_level
    )

    if (
        focus_changed
        or user_notes_changed
        or context_level_changed
        or not previous_generated_at
    ):
        return True

    elapsed_seconds = max((utc_now() - previous_generated_at).total_seconds(), 0.0)
    new_segment_count = max(current_segment_count - previous_segment_count, 0)
    new_word_count = max(current_word_count - previous_word_count, 0)

    if elapsed_seconds < MEETING_EDGE_MIN_REFRESH_SECONDS:
        return False

    return (
        new_segment_count >= MEETING_EDGE_MIN_NEW_SEGMENTS
        or new_word_count >= MEETING_EDGE_MIN_NEW_WORDS
    )


def _format_recording_timestamp(seconds: float) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(max(float(seconds), 0.0)))


def _load_recording_audio_chunks_impl(
    session, recording_id: int
) -> list[RecordingAudioChunk]:
    return session.exec(
        select(RecordingAudioChunk)
        .where(RecordingAudioChunk.recording_id == recording_id)
        .order_by(RecordingAudioChunk.sequence_no)
    ).all()


def _load_recording_audio_window_manifests_impl(
    session,
    recording_id: int,
) -> list[RecordingAudioWindowManifest]:
    return session.exec(
        select(RecordingAudioWindowManifest)
        .where(RecordingAudioWindowManifest.recording_id == recording_id)
        .order_by(RecordingAudioWindowManifest.window_index)
    ).all()


def _segment_requires_final_diarization_check(segment: dict) -> bool:
    speaker_label = str(segment.get("speaker") or "").strip().upper()
    speaker_state = str(segment.get("speaker_state") or "").strip().lower()
    speaker_confidence = _to_optional_float(segment.get("speaker_confidence"))

    if segment.get("provisional") is True:
        return True
    if speaker_label == "UNKNOWN":
        return True
    if speaker_state == ROLLING_DIARIZATION_SPEAKER_STATE_PROVISIONAL:
        return True
    if speaker_state == "" and str(segment.get("segment_source") or "") in {
        "live",
        "catch_up",
    }:
        return True
    if (
        speaker_confidence is not None
        and speaker_confidence < ROLLING_DIARIZATION_CONFIDENCE_FLOOR
    ):
        return True
    if list(segment.get("overlapping_speakers") or []):
        return True
    return False


def _is_unresolved_speaker_label(label: object) -> bool:
    return str(label or "").strip().upper() in {"", "UNKNOWN"}


def _collect_ordered_final_speaker_labels(final_segments: Sequence[dict]) -> list[str]:
    ordered_speakers: list[str] = []
    seen_speakers: set[str] = set()
    for seg in final_segments:
        speaker_label = str(seg.get("speaker") or "UNKNOWN")
        if (
            not _is_unresolved_speaker_label(speaker_label)
            and speaker_label not in seen_speakers
        ):
            ordered_speakers.append(speaker_label)
            seen_speakers.add(speaker_label)
        for overlapping_spk in seg.get("overlapping_speakers", []):
            overlapping_label = str(overlapping_spk or "UNKNOWN")
            if (
                _is_unresolved_speaker_label(overlapping_label)
                or overlapping_label in seen_speakers
            ):
                continue
            ordered_speakers.append(overlapping_label)
            seen_speakers.add(overlapping_label)
    return ordered_speakers


def _collect_low_confidence_diarization_spans(
    live_segments_for_reuse: Sequence[dict],
) -> list[dict[str, int]]:
    spans: list[dict[str, int]] = []
    for segment in live_segments_for_reuse:
        if not _segment_requires_final_diarization_check(segment):
            continue

        start_ms = max(
            0,
            int(round(float(segment.get("start", 0.0)) * 1000.0))
            - FINAL_DIARIZATION_SPAN_PADDING_MS,
        )
        end_ms = max(
            start_ms,
            int(round(float(segment.get("end", 0.0)) * 1000.0))
            + FINAL_DIARIZATION_SPAN_PADDING_MS,
        )

        if spans and start_ms <= (
            int(spans[-1]["end_ms"]) + FINAL_DIARIZATION_BRIDGE_GAP_MS
        ):
            spans[-1]["end_ms"] = max(int(spans[-1]["end_ms"]), end_ms)
            spans[-1]["segment_count"] = int(spans[-1].get("segment_count", 0)) + 1
            continue

        spans.append(
            {
                "start_ms": int(start_ms),
                "end_ms": int(end_ms),
                "segment_count": 1,
            }
        )
    return spans


def _count_distinct_live_reuse_speakers(
    live_segments_for_reuse: Sequence[dict],
) -> int:
    speaker_labels: set[str] = set()
    for segment in live_segments_for_reuse:
        speaker_label = str(segment.get("speaker") or "UNKNOWN")
        if _is_unresolved_speaker_label(speaker_label):
            continue
        speaker_labels.add(speaker_label)
    return len(speaker_labels)


def _extract_completed_window_speaker_labels(raw_payload: object) -> set[str]:
    if not isinstance(raw_payload, dict):
        return set()

    speaker_labels: set[str] = set()
    for label in raw_payload.get("speaker_labels") or []:
        label_text = str(label or "").strip()
        if label_text:
            speaker_labels.add(label_text)

    speaker_metadata = raw_payload.get("speaker_metadata") or {}
    if isinstance(speaker_metadata, dict):
        for label in speaker_metadata.keys():
            label_text = str(label or "").strip()
            if label_text:
                speaker_labels.add(label_text)

    for turn_payload in raw_payload.get("turns") or []:
        if not isinstance(turn_payload, dict):
            continue
        label_text = str(turn_payload.get("local_speaker_key") or "").strip()
        if label_text:
            speaker_labels.add(label_text)

    return speaker_labels


def _summarize_completed_diarization_window_speaker_evidence_rows(
    window_results: Sequence[object],
) -> dict[str, int]:
    evidence = {
        "completed_window_count": 0,
        "multi_speaker_window_count": 0,
        "max_speaker_count": 0,
    }

    for window_result in window_results:
        evidence["completed_window_count"] += 1
        speaker_count = len(
            _extract_completed_window_speaker_labels(
                getattr(window_result, "raw_payload", None)
            )
        )
        evidence["max_speaker_count"] = max(
            evidence["max_speaker_count"], speaker_count
        )
        if speaker_count > 1:
            evidence["multi_speaker_window_count"] += 1

    return evidence


def _summarize_completed_diarization_window_speaker_evidence_impl(
    session,
    *,
    recording_id: int,
    effective_from_ms: int = 0,
) -> dict[str, int]:
    if not hasattr(session, "exec"):
        return {
            "completed_window_count": 0,
            "multi_speaker_window_count": 0,
            "max_speaker_count": 0,
        }

    window_results = session.exec(
        select(DiarizationWindowResult)
        .where(DiarizationWindowResult.recording_id == recording_id)
        .where(DiarizationWindowResult.status == "completed")
        .where(DiarizationWindowResult.window_end_ms > int(effective_from_ms))
    ).all()
    return _summarize_completed_diarization_window_speaker_evidence_rows(window_results)


def _completed_window_speaker_evidence_requires_final_diarization(
    live_segments_for_reuse: Sequence[dict],
    completed_window_speaker_evidence: dict[str, int] | None,
) -> bool:
    if not completed_window_speaker_evidence:
        return False

    max_speaker_count = int(
        completed_window_speaker_evidence.get("max_speaker_count", 0) or 0
    )
    multi_speaker_window_count = int(
        completed_window_speaker_evidence.get("multi_speaker_window_count", 0) or 0
    )
    if max_speaker_count <= 1 or multi_speaker_window_count <= 0:
        return False

    live_speaker_count = _count_distinct_live_reuse_speakers(live_segments_for_reuse)
    return max_speaker_count > live_speaker_count


def _build_final_diarization_plan_impl(
    *,
    live_segments_for_reuse: Sequence[dict],
    reused_live_transcript_segments: Sequence[dict],
    engine_override: dict | None,
    completed_window_replay_available: bool = False,
    completed_window_speaker_evidence: dict[str, int] | None = None,
) -> dict[str, object]:
    if engine_override:
        return {
            "should_run": True,
            "reason": "engine_override",
            "low_confidence_spans": [],
        }

    if not reused_live_transcript_segments or not live_segments_for_reuse:
        return {
            "should_run": True,
            "reason": "no_live_reuse",
            "low_confidence_spans": [],
        }

    low_confidence_spans = _collect_low_confidence_diarization_spans(
        live_segments_for_reuse
    )
    if low_confidence_spans:
        return {
            "should_run": True,
            "reason": "low_confidence_spans",
            "low_confidence_spans": low_confidence_spans,
            "completed_window_replay_available": bool(
                completed_window_replay_available
            ),
        }

    if _completed_window_speaker_evidence_requires_final_diarization(
        live_segments_for_reuse,
        completed_window_speaker_evidence,
    ):
        return {
            "should_run": True,
            "reason": "completed_window_speaker_mismatch",
            "low_confidence_spans": [],
            "completed_window_replay_available": bool(
                completed_window_replay_available
            ),
        }

    return {
        "should_run": False,
        "reason": "confident_live_reuse",
        "low_confidence_spans": [],
        "completed_window_replay_available": bool(completed_window_replay_available),
    }


def _build_catch_up_segments_impl(
    *,
    session,
    recording: Recording,
    processed_audio_path: str,
    merged_config: dict,
    transcribe_audio,
    extract_audio_clip,
    temp_files: list[str],
    log: logging.Logger,
) -> tuple[list[dict], set[int], ProcessingRun | None]:
    manifest_rows = _load_recording_audio_window_manifests(session, recording.id)
    chunk_rows = _load_recording_audio_chunks(session, recording.id)
    raw_pending_spans = collect_pending_chunk_spans(manifest_rows, chunk_rows)
    pending_manifest_rows = [
        row
        for row in manifest_rows
        if row.id is not None and not window_asr_is_processed(row)
    ]
    pending_window_ids = {int(row.id) for row in pending_manifest_rows}
    if not raw_pending_spans and not pending_window_ids:
        return [], set(), None

    span_start_ms = min(
        [int(row.window_start_ms) for row in pending_manifest_rows]
        or [span.start_ms for span in raw_pending_spans],
        default=0,
    )
    span_end_ms = max(
        [int(row.window_end_ms) for row in pending_manifest_rows]
        or [span.end_ms for span in raw_pending_spans],
        default=0,
    )
    catch_up_idempotency_parts = (
        ",".join(
            f"{span.start_sequence}-{span.end_sequence}" for span in raw_pending_spans
        )
        if raw_pending_spans
        else f"windows:{','.join(str(window_id) for window_id in sorted(pending_window_ids))}"
    )
    catch_up_run = ensure_processing_run(
        session,
        recording_id=recording.id,
        run_kind=ProcessingRunKind.CATCH_UP,
        status=ProcessingRunStatus.RUNNING,
        trigger_source="worker",
        transcription_backend=merged_config.get("transcription_backend"),
        span_start_ms=span_start_ms,
        span_end_ms=span_end_ms,
        idempotency_key=(
            "catch_up:"
            f"{recording.id}:"
            f"{_final_asr_config_hash(merged_config)}:"
            f"{catch_up_idempotency_parts}"
        ),
    )
    catch_up_run.status = ProcessingRunStatus.RUNNING
    catch_up_run.completed_at = None
    catch_up_run.error_summary = None
    session.add(catch_up_run)

    catch_up_segments: list[dict] = []
    status_counts = count_manifest_statuses(manifest_rows)
    ledger_enabled = bool(config_manager.get("enable_asr_window_result_ledger", True))
    pending_spans: list = []
    reused_span_count = 0
    reused_segment_count = 0
    legacy_payload_gap_count = 0

    for span in raw_pending_spans:
        existing_result = None
        reusable_segments = None
        if ledger_enabled:
            existing_result = get_recording_asr_window_result(
                session,
                recording_id=recording.id,
                source_kind="catch_up",
                span_start_ms=span.start_ms,
                span_end_ms=span.end_ms,
                chunk_start_sequence=span.start_sequence,
                chunk_end_sequence=span.end_sequence,
                config=merged_config,
                config_hash=_final_asr_config_hash(merged_config),
            )
            reusable_segments = get_reusable_catch_up_segments(existing_result)

        if reusable_segments is not None:
            reused_span_count += 1
            reused_segment_count += len(reusable_segments)
            catch_up_segments.extend(reusable_segments)
            continue

        if ledger_enabled and existing_result is not None:
            status_value = getattr(
                existing_result.status, "value", existing_result.status
            )
            if status_value == "completed":
                legacy_payload_gap_count += 1

        pending_spans.append(span)

    record_pipeline_metric(
        stage="catch_up_detected",
        recording_id=recording.id,
        payload={
            "pending_window_count": len(pending_window_ids),
            "pending_span_count": len(raw_pending_spans),
            "rerun_span_count": len(pending_spans),
            "reused_span_count": reused_span_count,
            "reused_segment_count": reused_segment_count,
            "legacy_payload_gap_count": legacy_payload_gap_count,
            "window_status_counts": status_counts,
        },
        log=log,
    )

    for span in pending_spans:
        clip_path = os.path.join(
            os.path.dirname(processed_audio_path),
            f"catch_up_{recording.id}_{span.start_sequence}_{span.end_sequence}.wav",
        )
        extract_audio_clip(
            processed_audio_path,
            clip_path,
            start_seconds=span.start_ms / 1000.0,
            end_seconds=span.end_ms / 1000.0,
        )
        temp_files.append(clip_path)

        with pipeline_metric_timer(
            stage="catch_up_asr_span",
            recording_id=recording.id,
            payload={
                "start_sequence": span.start_sequence,
                "end_sequence": span.end_sequence,
                "span_start_ms": span.start_ms,
                "span_end_ms": span.end_ms,
                "engine": merged_config.get("transcription_backend"),
            },
            log=log,
        ) as metric:
            if ledger_enabled:
                start_recording_asr_window_result(
                    session,
                    recording_id=recording.id,
                    processing_run_id=catch_up_run.id if catch_up_run else None,
                    source_kind="catch_up",
                    span_start_ms=span.start_ms,
                    span_end_ms=span.end_ms,
                    chunk_start_sequence=span.start_sequence,
                    chunk_end_sequence=span.end_sequence,
                    config=merged_config,
                    config_hash=_final_asr_config_hash(merged_config),
                )
            try:
                result = transcribe_audio(clip_path, config=merged_config)
            except Exception as exc:
                if ledger_enabled:
                    fail_recording_asr_window_result(
                        session,
                        recording_id=recording.id,
                        processing_run_id=catch_up_run.id if catch_up_run else None,
                        source_kind="catch_up",
                        span_start_ms=span.start_ms,
                        span_end_ms=span.end_ms,
                        chunk_start_sequence=span.start_sequence,
                        chunk_end_sequence=span.end_sequence,
                        config=merged_config,
                        config_hash=_final_asr_config_hash(merged_config),
                        error_summary=str(exc).strip()[:500]
                        or "Catch-up ASR invocation failed.",
                        error_payload={"error_type": exc.__class__.__name__},
                    )
                raise
            metric["payload"]["segment_count"] = len((result or {}).get("segments", []))

        result_segments: list[dict] = []
        for segment in (result or {}).get("segments", []):
            text = str(segment.get("text", "")).strip()
            if not text:
                continue

            relative_start = float(segment.get("start", 0.0) or 0.0)
            relative_end = float(segment.get("end", 0.0) or 0.0)
            if relative_end <= relative_start:
                continue

            result_segments.append(
                {
                    "start": relative_start,
                    "end": relative_end,
                    "speaker": str(segment.get("speaker") or "UNKNOWN"),
                    "text": text,
                    "segment_source": "catch_up",
                }
            )
            catch_up_segments.append(
                {
                    "start": span.start_ms / 1000.0 + relative_start,
                    "end": span.start_ms / 1000.0 + relative_end,
                    "speaker": str(segment.get("speaker") or "UNKNOWN"),
                    "text": text,
                    "segment_source": "catch_up",
                }
            )

        if ledger_enabled:
            if result is None:
                fail_recording_asr_window_result(
                    session,
                    recording_id=recording.id,
                    processing_run_id=catch_up_run.id if catch_up_run else None,
                    source_kind="catch_up",
                    span_start_ms=span.start_ms,
                    span_end_ms=span.end_ms,
                    chunk_start_sequence=span.start_sequence,
                    chunk_end_sequence=span.end_sequence,
                    config=merged_config,
                    config_hash=_final_asr_config_hash(merged_config),
                    error_summary="Catch-up ASR returned no result.",
                    error_payload={"error_type": "empty_result"},
                )
            else:
                complete_recording_asr_window_result(
                    session,
                    recording_id=recording.id,
                    processing_run_id=catch_up_run.id if catch_up_run else None,
                    source_kind="catch_up",
                    span_start_ms=span.start_ms,
                    span_end_ms=span.end_ms,
                    chunk_start_sequence=span.start_sequence,
                    chunk_end_sequence=span.end_sequence,
                    config=merged_config,
                    config_hash=_final_asr_config_hash(merged_config),
                    result_payload={
                        "segment_count": len(result_segments),
                        "text_chars": len((result or {}).get("text") or ""),
                        "segments": result_segments,
                    },
                )

    catch_up_segments.sort(
        key=lambda segment: (
            float(segment.get("start", 0.0)),
            float(segment.get("end", 0.0)),
            str(segment.get("text", "")),
        )
    )

    return catch_up_segments, pending_window_ids, catch_up_run


def _recording_has_completed_diarization_windows_impl(
    session,
    *,
    recording_id: int,
    effective_from_ms: int = 0,
) -> bool:
    return (
        session.exec(
            select(DiarizationWindowResult)
            .where(DiarizationWindowResult.recording_id == recording_id)
            .where(DiarizationWindowResult.status == "completed")
            .where(DiarizationWindowResult.window_end_ms > int(effective_from_ms))
            .limit(1)
        ).first()
        is not None
    )


def _build_diarization_window_payload(
    diarization_result,
    *,
    window_start_ms: int,
    window_end_ms: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    turn_payloads: list[dict[str, object]] = []
    speaker_labels: set[str] = set()

    if diarization_result is not None and hasattr(diarization_result, "itertracks"):
        for segment, track, label in diarization_result.itertracks(yield_label=True):
            start_ms = window_start_ms + int(round(float(segment.start) * 1000.0))
            end_ms = window_start_ms + int(round(float(segment.end) * 1000.0))
            if end_ms <= start_ms:
                continue
            label_value = str(label)
            speaker_labels.add(label_value)
            turn_payloads.append(
                {
                    "local_speaker_key": label_value,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "track": str(track),
                }
            )

    turn_payloads.sort(
        key=lambda payload: (
            int(payload["start_ms"]),
            int(payload["end_ms"]),
            str(payload["local_speaker_key"]),
        )
    )
    return (
        {
            "window_start_ms": int(window_start_ms),
            "window_end_ms": int(window_end_ms),
            "speaker_labels": sorted(speaker_labels),
            "turn_count": len(turn_payloads),
            "turns": turn_payloads,
        },
        turn_payloads,
    )


def _catch_up_diarization_config_hash(merged_config: dict) -> str:
    return build_rolling_diarization_config_hash(
        merged_config,
        target_window_ms=int(
            merged_config.get("rolling_diarization_window_ms", 20_000)
        ),
        hop_ms=int(merged_config.get("rolling_diarization_hop_ms", 5_000)),
    )


def _persist_catch_up_diarization_window_impl(
    session,
    *,
    recording_id: int,
    manifest_row: RecordingAudioWindowManifest,
    processing_run_id: int | None,
    diarization_result,
    merged_config: dict,
    device: str,
    error_message: str | None = None,
) -> DiarizationWindowResult:
    return persist_diarization_window_result(
        session,
        recording_id=recording_id,
        manifest_row=manifest_row,
        processing_run_id=processing_run_id,
        diarization_result=diarization_result,
        config_hash=_catch_up_diarization_config_hash(merged_config),
        device=device,
        model_name=get_rolling_diarization_model_name(),
        error_message=error_message,
    )


def _run_catch_up_diarization_windows_impl(
    *,
    session,
    recording: Recording,
    processed_audio_path: str,
    merged_config: dict,
    diarize_audio,
    extract_audio_clip,
    processing_run_id: int | None,
    temp_files: list[str],
    log: logging.Logger,
) -> tuple[set[int], set[int]]:
    manifest_rows = _load_recording_audio_window_manifests(session, recording.id)
    config_hash = _catch_up_diarization_config_hash(merged_config)
    completed_window_indexes = {
        int(window_index)
        for window_index in session.exec(
            select(DiarizationWindowResult.window_index)
            .where(DiarizationWindowResult.recording_id == recording.id)
            .where(DiarizationWindowResult.config_hash == config_hash)
            .where(DiarizationWindowResult.status == "completed")
        ).all()
    }
    pending_manifest_rows = [
        row
        for row in manifest_rows
        if row.id is not None
        and window_asr_is_processed(row)
        and int(row.window_index) not in completed_window_indexes
        and not window_diarization_is_processed(
            row,
            config_hash=config_hash,
        )
    ]
    if not pending_manifest_rows:
        return set(), set()

    completed_window_ids: set[int] = set()
    failed_window_ids: set[int] = set()
    device = str(merged_config.get("processing_device", "auto"))

    for manifest_row in pending_manifest_rows:
        clip_path = os.path.join(
            os.path.dirname(processed_audio_path),
            f"catch_up_diarize_{recording.id}_{manifest_row.window_index}.wav",
        )
        extract_audio_clip(
            processed_audio_path,
            clip_path,
            start_seconds=float(manifest_row.window_start_ms) / 1000.0,
            end_seconds=float(manifest_row.window_end_ms) / 1000.0,
        )
        temp_files.append(clip_path)

        with pipeline_metric_timer(
            stage="catch_up_diarization_window",
            recording_id=recording.id,
            payload={
                "window_index": int(manifest_row.window_index),
                "window_start_ms": int(manifest_row.window_start_ms),
                "window_end_ms": int(manifest_row.window_end_ms),
                "chunk_start_sequence": int(manifest_row.chunk_start_sequence),
                "chunk_end_sequence": int(manifest_row.chunk_end_sequence),
            },
            log=log,
        ) as metric:
            diarization_result = diarize_audio(clip_path, config=merged_config)
            metric["payload"]["result_available"] = diarization_result is not None

        error_message = None
        if diarization_result is None:
            error_message = "Catch-up diarization returned no result"

        window_result = _persist_catch_up_diarization_window(
            session,
            recording_id=recording.id,
            manifest_row=manifest_row,
            processing_run_id=processing_run_id,
            diarization_result=diarization_result,
            merged_config=merged_config,
            device=device,
            error_message=error_message,
        )

        manifest_row.diarization_processing_run_id = processing_run_id
        manifest_row.diarization_config_hash = config_hash
        manifest_row.diarization_window_result_id = window_result.id
        manifest_row.processing_run_id = processing_run_id
        if error_message:
            manifest_row.diarization_status = WINDOW_DIARIZATION_STATUS_FAILED
            manifest_row.diarization_last_error = error_message
            manifest_row.status = WINDOW_STATUS_FAILED
            manifest_row.last_error = error_message
            failed_window_ids.add(int(manifest_row.id))
        else:
            manifest_row.diarization_status = WINDOW_DIARIZATION_STATUS_PROCESSED
            manifest_row.diarization_last_error = None
            manifest_row.last_error = None
            completed_window_ids.add(int(manifest_row.id))
        session.add(manifest_row)

    return completed_window_ids, failed_window_ids


def _build_automatic_meeting_intelligence_transcript_impl(
    segments: Sequence[dict],
    speaker_map: dict[str, str],
    unresolved_speakers: Sequence[str],
) -> str:
    unresolved_labels = set(unresolved_speakers)
    lines: list[str] = []

    for seg in segments:
        speaker_label = str(seg.get("speaker", "Unknown"))
        display_name = (
            speaker_label
            if speaker_label in unresolved_labels
            else speaker_map.get(speaker_label, speaker_label)
        )

        overlapping_names = []
        for overlapping_label in seg.get("overlapping_speakers", []):
            normalized_label = str(overlapping_label)
            if normalized_label in unresolved_labels:
                overlapping_names.append(normalized_label)
            else:
                overlapping_names.append(
                    speaker_map.get(normalized_label, normalized_label)
                )

        overlapping_suffix = (
            f" (with {', '.join(overlapping_names)})" if overlapping_names else ""
        )
        text = str(seg.get("text", "")).strip()
        lines.append(
            f"[{_format_recording_timestamp(seg.get('start', 0))} - "
            f"{_format_recording_timestamp(seg.get('end', seg.get('start', 0)))}] "
            f"{display_name}{overlapping_suffix}: {text}"
        )

    return "\n".join(lines)


def _apply_automatic_meeting_intelligence_result(
    session,
    recording: Recording,
    transcript: Transcript,
    speakers: Sequence[RecordingSpeaker],
    result: AutomaticMeetingIntelligenceResult,
    *,
    meeting_context: MeetingEventContext | None,
    provider: str | None,
) -> None:
    from backend.processing.embedding import cosine_similarity

    segments = [
        dict(segment)
        for segment in (transcript.segments or [])
        if isinstance(segment, dict)
    ]
    eligible_labels = get_speakers_eligible_for_llm_renaming(speakers)

    embedding_similarity_scores: dict[str, float] = {}
    for speaker in speakers:
        if not speaker.embedding or not speaker.diarization_label:
            continue
        from backend.models.speaker import GlobalSpeaker

        global_speaker = None
        if speaker.global_speaker_id:
            global_speaker = session.get(GlobalSpeaker, speaker.global_speaker_id)
        if global_speaker and global_speaker.embedding:
            score = cosine_similarity(speaker.embedding, global_speaker.embedding)
            embedding_similarity_scores[speaker.diarization_label] = score

    llm_result = build_mapping_based_speaker_suggestions(
        result.speaker_mapping,
        segments=segments,
        eligible_labels=eligible_labels,
        meeting_context=meeting_context,
        source="llm",
        embedding_similarity_scores=embedding_similarity_scores,
    )

    suggestion_count = 0
    suggestion_count += _persist_generated_speaker_name_suggestions(
        session,
        recording=recording,
        transcript=transcript,
        speakers=speakers,
        inference_result=llm_result,
        origin="automatic_meeting_intelligence",
        provider=provider,
        replaced_reason="automatic_meeting_intelligence_refresh",
    )
    superseded_count = _supersede_pending_speaker_name_suggestions_for_labels(
        session,
        transcript=transcript,
        diarization_labels=(
            label for label in eligible_labels if label not in llm_result.mapping
        ),
        reason="automatic_meeting_intelligence_omitted_by_llm",
    )

    recording.name = result.title
    transcript.notes = result.notes_markdown
    transcript.notes_status = "completed"
    transcript.error_message = None
    session.add(recording)
    session.add(transcript)
    session.commit()
    record_pipeline_metric(
        stage="speaker_name_suggestions_generated",
        recording_id=recording.id,
        payload={
            "origin": "automatic_meeting_intelligence",
            "suggestion_count": suggestion_count,
            "superseded_count": superseded_count,
            "rule_based_count": 0,
            "llm_count": len(llm_result.suggestions),
        },
        log=logger,
    )
    update_recording_status(session, recording.id)


def _resolve_meeting_event_context_impl(
    session,
    recording: Recording,
) -> MeetingEventContext | None:
    """Load the linked calendar event for a recording and build its context.

    Returns ``None`` when no event is linked, so the prompt paths fall back to
    the unchanged "no context" string.
    """
    if recording.calendar_event_id is None:
        return None
    try:
        event = session.get(CalendarEvent, recording.calendar_event_id)
        return meeting_event_context_from_calendar_event(event)
    except Exception:
        logger.exception(
            "Failed to load calendar event context for recording %s", recording.id
        )
        return None


def _set_meeting_edge_state(
    session,
    transcript: Transcript,
    *,
    status: str,
    error_message: str | None = None,
    source_signature: str | None = None,
    payload: dict | None = None,
) -> None:
    transcript.meeting_edge_status = status
    transcript.meeting_edge_error_message = error_message
    if source_signature is not None:
        transcript.meeting_edge_source_signature = source_signature
    if payload is not None:
        transcript.meeting_edge_payload = payload
        flag_modified(transcript, "meeting_edge_payload")
    session.add(transcript)
    session.commit()


def _run_automatic_meeting_intelligence_stage_impl(
    *,
    session,
    task: Task | None,
    recording: Recording,
    transcript: Transcript,
    speakers: Sequence[RecordingSpeaker],
    transcript_text: str,
    unresolved_speakers: Sequence[str],
    llm_config: ResolvedLLMConfig,
    prefer_short_titles: bool,
    device_suffix: str,
    detected_transcription_language: str | None = None,
) -> AutomaticMeetingIntelligenceResult | None:
    cleaned_transcript = transcript_text.strip()
    meeting_context = _resolve_meeting_event_context(session, recording)
    deterministic_result = detect_rule_based_speaker_suggestions(
        [
            dict(segment)
            for segment in (transcript.segments or [])
            if isinstance(segment, dict)
        ],
        unresolved_speakers,
        meeting_context,
    )
    if not cleaned_transcript:
        suggestion_count = _persist_generated_speaker_name_suggestions(
            session,
            recording=recording,
            transcript=transcript,
            speakers=speakers,
            inference_result=deterministic_result,
            origin="automatic_meeting_intelligence",
            provider=None,
            replaced_reason="automatic_meeting_intelligence_refresh",
        )
        if suggestion_count:
            session.commit()
            record_pipeline_metric(
                stage="speaker_name_suggestions_generated",
                recording_id=recording.id,
                payload={
                    "origin": "automatic_meeting_intelligence",
                    "suggestion_count": suggestion_count,
                    "rule_based_count": len(deterministic_result.suggestions),
                    "llm_count": 0,
                },
                log=logger,
            )
        logger.info(
            "Skipping automatic meeting intelligence for recording %s: transcript is empty",
            recording.id,
        )
        return None

    missing_llm_config = llm_config.missing_configuration_message()
    if missing_llm_config:
        logger.warning(
            "Skipping automatic meeting intelligence for recording %s: %s",
            recording.id,
            missing_llm_config,
        )
        suggestion_count = _persist_generated_speaker_name_suggestions(
            session,
            recording=recording,
            transcript=transcript,
            speakers=speakers,
            inference_result=deterministic_result,
            origin="automatic_meeting_intelligence",
            provider=None,
            replaced_reason="automatic_meeting_intelligence_refresh",
        )
        if suggestion_count:
            session.commit()
            record_pipeline_metric(
                stage="speaker_name_suggestions_generated",
                recording_id=recording.id,
                payload={
                    "origin": "automatic_meeting_intelligence",
                    "suggestion_count": suggestion_count,
                    "rule_based_count": len(deterministic_result.suggestions),
                    "llm_count": 0,
                },
                log=logger,
            )
        return None

    language_preferences = resolve_language_preferences(
        llm_config.merged_config,
        transcription_backend=llm_config.merged_config.get("transcription_backend"),
        detected_transcription_language=detected_transcription_language,
    )
    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript=cleaned_transcript,
        unresolved_speakers=tuple(unresolved_speakers),
        user_notes=transcript.user_notes,
        prefer_short_titles=prefer_short_titles,
        meeting_context=meeting_context,
        output_language_instruction=language_preferences.notes_language_instruction,
    )

    if task is not None:
        task.update_state(
            state="PROCESSING",
            meta={
                "progress": AUTOMATIC_MEETING_INTELLIGENCE_PROGRESS,
                "stage": AUTOMATIC_MEETING_INTELLIGENCE_STAGE,
            },
        )

    recording.processing_step = f"{AUTOMATIC_MEETING_INTELLIGENCE_STEP}{device_suffix}"
    recording.processing_progress = AUTOMATIC_MEETING_INTELLIGENCE_PROGRESS
    transcript.notes_status = "generating"
    transcript.error_message = None
    session.add(recording)
    session.add(transcript)
    session.commit()
    update_recording_status(session, recording.id)

    try:
        llm = _llm_backend_from_config(llm_config)
        result = llm.generate_meeting_intelligence(
            request,
            timeout=AUTOMATIC_MEETING_INTELLIGENCE_TIMEOUT_SECONDS,
        )
        _apply_automatic_meeting_intelligence_result(
            session,
            recording,
            transcript,
            speakers,
            result,
            meeting_context=meeting_context,
            provider=llm_config.provider,
        )
        logger.info(
            "Generated unified meeting intelligence for recording %s",
            recording.id,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to generate automatic meeting intelligence for recording %s: %s",
            recording.id,
            exc,
        )
        _mark_notes_generation_error(session, recording, transcript, exc)
        return None


__all__ = [name for name in globals() if not name.startswith("__")]
