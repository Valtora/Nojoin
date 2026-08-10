from .constants import *
from .speaker_assignment import assign_and_identify_speakers

# ---------------------------------------------------------------------------
# process_recording_task orchestration stages
#
# The canonical finalize pipeline is decomposed into explicit stages with typed
# inputs and outputs. The stages run inside the task's try/except/finally, so
# Celery retry and ack handling, temp-file cleanup and VRAM release all stay in
# one place. Heavy ML imports (whisper, pyannote, torch, embeddings) must stay
# INSIDE the stage functions so the API process never pays for them at import
# time.
# ---------------------------------------------------------------------------


@dataclass
class _PipelineRunContext:
    """Shared handles threaded through the orchestration stages.

    ``task`` is the bound Celery task, used for ``update_state`` progress
    reporting; ``temp_files`` is the running cleanup list the finally block
    drains. These are deliberately shared mutable references: stages mutate
    ``recording`` and ``temp_files`` in place, so each stage's database writes
    and progress emissions are visible to the ones that follow it.
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

        vad_input_path = preprocess_audio_for_vad(audio_path)
        if not vad_input_path:
            raise RuntimeError("VAD preprocessing failed")
        temp_files.append(vad_input_path)

        vad_output_path = vad_input_path.replace("_vad.wav", "_vad_processed.wav")
        vad_success, speech_duration = mute_non_speech_segments(
            vad_input_path, vad_output_path
        )

        if not vad_success:
            raise RuntimeError("VAD execution failed")
        temp_files.append(vad_output_path)

        if speech_duration < 1.0:
            logger.warning(
                f"No speech detected in recording {recording_id} (speech duration: {speech_duration}s)"
            )
            recording.status = RecordingStatus.PROCESSED
            recording.client_status = ClientStatus.IDLE
            recording.processing_step = "Completed (No speech detected)"
            recording.processing_completed_at = utc_now()

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

    if engine_override:
        merged_config.update(engine_override)
        logger.info("Reprocess: engine override applied: %s", engine_override)

    transcription_result = None

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
    from backend.processing.diarization_stats import summarize_diarization_speakers
    from backend.processing.diarize import diarize_audio
    from backend.processing.speaker_cap import normalize_speaker_cap

    session = ctx.session
    merged_config = ctx.merged_config
    recording_id = ctx.recording_id

    enable_diarization = merged_config.get("enable_diarization", True)
    diarization_result = None
    # Per-recording upper bound. None keeps the unconstrained auto-detect path.
    speaker_cap = normalize_speaker_cap(getattr(recording, "max_speakers", None))

    if enable_diarization:
        ctx.task.update_state(
            state="PROCESSING", meta={"progress": 70, "stage": "Diarization"}
        )
        recording.processing_step = f"Determining who said what...{ctx.device_suffix}"
        recording.processing_progress = 70
        session.add(recording)
        session.commit()

        with pipeline_metric_timer(
            stage="final_diarization_invocation",
            recording_id=recording_id,
            payload={
                "input_path": processed_audio_path,
                "enabled": True,
                "max_speakers": speaker_cap,
            },
            log=logger,
        ) as metric:
            diarization_result = diarize_audio(
                processed_audio_path,
                config=merged_config,
                max_speakers=speaker_cap,
            )
            metric["payload"]["result_available"] = diarization_result is not None

        if diarization_result is None:
            msg = "Diarization failed (check HF token), falling back to single speaker."
            logger.warning(msg)
            recording.processing_step = msg
            session.add(recording)
            session.commit()
        else:
            from backend.processing.phantom_filter import filter_phantom_speakers

            try:
                diarization_result = filter_phantom_speakers(
                    diarization_result, processed_audio_path, config=merged_config
                )
            except Exception as e:  # noqa: BLE001 -- boundary: phantom filter is best-effort
                logger.warning(
                    f"Phantom speaker filter failed, continuing with unfiltered result: {e}"
                )

            # How much speech each cluster actually holds. This is what shows
            # whether an unexpected extra speaker is a negligible fragment or a
            # substantial one the phantom filter was never going to catch.
            try:
                record_pipeline_metric(
                    stage="final_diarization_speaker_stats",
                    recording_id=recording_id,
                    payload=summarize_diarization_speakers(
                        diarization_result, max_speakers=speaker_cap
                    ),
                    log=logger,
                )
            except Exception as e:  # noqa: BLE001 -- boundary: metrics are best-effort
                logger.warning("Could not record diarization speaker stats: %s", e)
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

    combined_segments = []
    if transcription_result:
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
        if enable_diarization and diarization_result:
            logger.warning(
                "Combination failed despite having diarization result. Using raw transcription segments with UNKNOWN speaker."
            )
        else:
            logger.info(
                "Using raw transcription segments (Diarization disabled or failed)."
            )

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

    # Keep the diarization_label in the segments to maintain the link to
    # RecordingSpeaker; the frontend resolves the display name from the map.
    updated_segments = list(final_segments)

    ctx.task.update_state(
        state="PROCESSING", meta={"progress": 92, "stage": "Finalizing"}
    )
    recording.processing_step = f"Finalizing transcript structure...{device_suffix}"
    recording.processing_progress = 92
    session.add(recording)
    session.commit()

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

        # Frame-level segmentation safety net for utterances that span a
        # speaker change but slipped through rolling diarization's coarser
        # turn boundaries.
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

    if transcript_text.strip() and _meeting_intelligence_runs_on_io(llm_config):
        # Non-local provider (cloud API or CLI OAuth subscription): the LLM call
        # is network-bound and needs no GPU, and the CLI OAuth SDK only lives in
        # the IO image. Free the GPU lane by generating notes on the IO worker.
        # The recording is otherwise finished; only notes remain pending, exactly
        # as in manual regeneration, so the GPU task still marks it Completed.
        transcript.notes_status = "generating"
        transcript.error_message = None
        session.add(transcript)
        session.commit()
        celery_app.send_task(
            "backend.worker.tasks.generate_meeting_intelligence_task",
            args=[recording.id],
        )
        logger.info(
            "Deferred meeting intelligence for recording %s to the IO lane "
            "(provider=%s)",
            recording.id,
            llm_config.provider,
        )
        return

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


def _release_asr_vram() -> None:
    """Free the ASR models once transcription is done, before diarization runs.

    Only on a GPU host, where VRAM is the contended resource. ONNX Runtime's arena
    grows with the transcription window and never shrinks, so on a small card a
    finished ASR session can leave diarization with nothing left to allocate. The
    engines reload lazily on the next task.
    """
    from backend.processing.onnx_providers import gpu_is_present

    if not gpu_is_present():
        return

    import gc

    import torch

    try:
        from backend.processing.transcribe import release_model_cache

        release_model_cache()
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("Released ASR VRAM before diarization.")
    except Exception as e:  # noqa: BLE001 -- boundary: VRAM release is best-effort
        logger.error("Error releasing ASR VRAM: %s", e)


def _release_pipeline_vram() -> None:
    """Best-effort release of cached ML models / VRAM after the task.

    Heavy ML imports stay inside this helper so importing the worker module
    never loads torch/whisper/pyannote. Wrapped so cleanup never crashes the
    task's finally block.
    """
    import torch

    try:
        logger.info("Releasing VRAM (keep_models_loaded=False)...")

        from backend.processing.transcribe import release_model_cache

        release_model_cache()

        from backend.processing.diarize import release_pipeline_cache

        release_pipeline_cache()

        from backend.processing.embedding_core import release_embedding_model_cache

        release_embedding_model_cache()

        from backend.processing.segmentation_refinement import (
            release_segmentation_model_cache,
        )

        release_segmentation_model_cache()

        from backend.processing.text_embedding import release_embedding_model

        release_embedding_model()

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
    owns Celery retry and error semantics, temp-file cleanup, and VRAM release.
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

    # Clear any live ASR model left warm by another capture before this finalise
    # loads its diarisation stack, so the two never exceed the 8 GB GPU budget.
    if not config_manager.get("keep_models_loaded", False):
        _release_pipeline_vram()

    user_settings = {}
    if recording.user_id:
        user = session.get(User, recording.user_id)
        if user and user.settings:
            user_settings = user.settings
            logger.info(
                f"Loaded settings for user {user.username}: {list(user_settings.keys())}"
            )

    llm_config = resolve_llm_config(session, user_settings, user_id=recording.user_id)
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
        # Stamp the task actually doing the work, not just the one the API dispatched.
        # The startup sweep uses this to tell a live run from one whose worker died,
        # and the worker's own re-queue path never set it.
        recording.celery_task_id = self.request.id
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

        # Hand the card back before diarization asks for it.
        _release_asr_vram()

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
        assign_and_identify_speakers(ctx, recording, final_segments, diarization_result)

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

        from backend.worker.tasks.followups import (
            dispatch_post_processing_followups,
        )

        dispatch_post_processing_followups(recording_id)

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


def _live_task_ids() -> set[str] | None:
    """Ids of tasks currently running on any worker.

    Returns None when that cannot be established, which is deliberately different
    from an empty set: callers must not treat "nobody answered" as "nothing is
    running", or restarting one worker would reclaim another's live work.
    """
    from backend.celery_app import celery_app

    try:
        active = celery_app.control.inspect(timeout=5.0).active()
    except Exception as e:  # noqa: BLE001 -- boundary: broker may be unreachable
        logger.warning("Could not inspect active Celery tasks: %s", e)
        return None

    if active is None:
        logger.warning("No Celery worker answered the active-task inspection.")
        return None

    return {
        task["id"]
        for tasks in active.values()
        for task in tasks
        if isinstance(task, dict) and task.get("id")
    }


def _reclaim_orphaned_processing(session) -> list[Recording]:
    """Return recordings left in PROCESSING by a worker that died mid-pipeline.

    A recording only leaves PROCESSING when its task finishes, so one whose task is
    no longer running is stranded: the startup sweep used to ignore it and the
    reprocess endpoint refuses to touch a PROCESSING recording, leaving no way back
    short of editing the database. Reclaiming needs proof the task is gone, so this
    returns nothing when liveness cannot be established.
    """
    stuck = session.exec(
        select(Recording).where(Recording.status == RecordingStatus.PROCESSING)
    ).all()
    if not stuck:
        return []

    live = _live_task_ids()
    if live is None:
        logger.warning(
            "%s recording(s) are PROCESSING but liveness is unknown; leaving them "
            "alone rather than risk reclaiming a running job.",
            len(stuck),
        )
        return []

    orphaned = [r for r in stuck if r.celery_task_id not in live]
    for recording in orphaned:
        logger.warning(
            "Recording %s was left PROCESSING by task %s, which is no longer "
            "running. Re-queueing.",
            recording.id,
            recording.celery_task_id or "<unrecorded>",
        )
        recording.status = RecordingStatus.QUEUED
        recording.processing_step = "Queued after an interrupted run..."
        session.add(recording)
    if orphaned:
        session.commit()
    return orphaned


def _sweeps_recordings(sender) -> bool:
    """Whether this worker should run the startup sweep.

    Every lane imports this module, so all three would otherwise sweep on startup
    and dispatch each pending recording once per lane. Only the lane that consumes
    the queue process_recording_task routes to can actually run the work, so it
    does the sweep. When the consumed queues cannot be read, sweep anyway: a
    duplicate run wastes time, but skipping leaves recordings stranded.
    """
    from backend.celery_app import GPU_QUEUE

    try:
        consume_from = getattr(sender.app.amqp.queues, "consume_from", None)
    except Exception:  # noqa: BLE001 -- boundary: internal Celery shape may change
        consume_from = None

    if not consume_from:
        logger.warning("Could not read this worker's queues; sweeping regardless.")
        return True
    return GPU_QUEUE in set(consume_from)


@worker_ready.connect
def check_queued_recordings(sender, **kwargs):
    """
    On worker startup, re-queue recordings left QUEUED, and reclaim any left
    PROCESSING by a worker that died mid-pipeline.
    """
    if not _sweeps_recordings(sender):
        return

    logger.info("Checking for pending QUEUED recordings...")
    session = get_sync_session()
    try:
        statement = select(Recording).where(Recording.status == RecordingStatus.QUEUED)
        recordings = list(session.exec(statement).all())
        recordings.extend(_reclaim_orphaned_processing(session))

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


# Providers that run on the user's own hardware. Their meeting-intelligence
# generation stays inline on the GPU worker. Every other provider (cloud APIs,
# CLI OAuth subscriptions) is network-bound, needs no GPU, and — for CLI OAuth —
# relies on the SDK that only ships in the IO image, so its generation is
# deferred to the IO lane.
_LOCAL_LLM_PROVIDERS = frozenset({"ollama"})


def _meeting_intelligence_runs_on_io(llm_config: ResolvedLLMConfig) -> bool:
    """True when the resolved primary provider should generate notes on the IO
    lane rather than inline on the GPU pipeline.

    Only a real, usable non-local LLM call is worth deferring: a missing/blank
    provider or one with incomplete configuration is left to run inline, where
    the stage cheaply falls back to rule-based speaker suggestions without a
    network call. This also keeps the deferred IO task on its happy path, so it
    always drives notes_status to a terminal state.
    """
    provider = (llm_config.provider or "").strip().lower()
    if not provider or provider in _LOCAL_LLM_PROVIDERS:
        return False
    return not llm_config.missing_configuration_message()


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


__all__ = [name for name in globals() if not name.startswith("__")]
