from .constants import *

@celery_app.task(name="backend.worker.tasks.generate_notes_task", base=DatabaseTask, bind=True)
def generate_notes_task(self, recording_id: int):
    """
    Generate meeting notes for a recording.
    """
    session = self.session
    recording = None
    transcript = None
    try:
        recording = session.get(Recording, recording_id)
        if not recording:
            logger.error(f"Recording {recording_id} not found.")
            return

        transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
        if not transcript:
            logger.error(f"Transcript for recording {recording_id} not found.")
            return

        # Update status
        transcript.notes_status = "generating"
        transcript.error_message = None
        recording.processing_step = "Generating meeting notes..."
        recording.processing_progress = 97
        session.add(transcript)
        session.add(recording)
        session.commit()
        update_recording_status(session, recording.id)

        # Get User Settings
        user_settings = {}
        if recording.user_id:
            user = session.get(User, recording.user_id)
            if user and user.settings:
                user_settings = user.settings

        llm_config = resolve_llm_config(session, user_settings)
        missing_llm_config = llm_config.missing_configuration_message()
        if missing_llm_config:
            logger.warning("Cannot generate notes: %s", missing_llm_config)
            _mark_notes_generation_error(session, recording, transcript, missing_llm_config)
            return

        segments = build_transcript_segments_for_read(
            session,
            recording_id,
            transcript=transcript,
        )
        if not segments:
            _mark_notes_generation_error(session, recording, transcript, "Transcript is empty")
            return

        # Build Speaker Map and Transcript Text
        speakers = session.exec(select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)).all()
        speaker_map = build_recording_speaker_map(speakers)
        transcript_text = format_segments_for_llm(segments, speaker_map)

        # Call LLM Service
        llm = _llm_backend_from_config(llm_config)
        notes = llm.generate_meeting_notes(
            transcript_text,
            speaker_map,
            timeout=300,
            user_notes=transcript.user_notes,
            meeting_context=_resolve_meeting_event_context(session, recording),
        )

        # Save Notes
        transcript.notes = notes
        transcript.notes_status = "completed"
        transcript.error_message = None
        recording.processing_step = "Completed"
        recording.processing_progress = 100
        session.add(transcript)
        session.add(recording)
        session.commit()
        update_recording_status(session, recording.id)
        logger.info(f"Generated meeting notes for recording {recording_id}")

        # --- Index Notes for RAG ---
        try:
            # Clean up existing note chunks
            existing_chunks = session.exec(
                select(ContextChunk)
                .where(ContextChunk.recording_id == recording_id)
                .where(ContextChunk.document_id == None)
            ).all()
            
            for chunk in existing_chunks:
                if chunk.meta and chunk.meta.get('source') == 'notes':
                    session.delete(chunk)
            
            # Chunking
            from backend.processing.text_embedding import get_text_embedding_service
            
            note_chunks = []
            CHUNK_SIZE = 1000
            OVERLAP = 100
            
            if notes:
                start = 0
                while start < len(notes):
                    end = start + CHUNK_SIZE
                    note_chunks.append(notes[start:end])
                    start += (CHUNK_SIZE - OVERLAP)
            
            if note_chunks:
                embedding_service = get_text_embedding_service()
                vectors = embedding_service.embed(note_chunks)
                
                for i, (text_chunk, vector) in enumerate(zip(note_chunks, vectors)):
                    db_chunk = ContextChunk(
                        recording_id=recording_id,
                        content=text_chunk,
                        embedding=vector,
                        meta={"chunk_index": i, "source": "notes"}
                    )
                    session.add(db_chunk)
                session.commit()
                logger.info(f"Indexed {len(note_chunks)} note chunks for recording {recording_id}")

        except Exception as e:
            logger.error(f"Failed to index meeting notes for RAG: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Failed to generate meeting notes: {e}", exc_info=True)
        session.rollback()
        if transcript is None:
            transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
        _mark_notes_generation_error(session, recording, transcript, e)


@celery_app.task(name="backend.worker.tasks.infer_speakers_task", base=DatabaseTask, bind=True)
def infer_speakers_task(self, recording_id: int):
    """
    Independent task to re-run speaker inference using LLM.
    """
    # Reload config
    config_manager.reload()
    
    session = self.session
    try:
        recording = session.get(Recording, recording_id)
        if not recording:
            logger.error(f"Recording {recording_id} not found.")
            return

        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).first()
        segments = build_transcript_segments_for_read(
            session,
            recording_id,
            transcript=transcript,
        )
        if not transcript or not segments:
            logger.error(f"No transcript found for recording {recording_id}.")
            _complete_speaker_inference_task(session, recording)
            return

        speakers = session.exec(
            select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)
        ).all()
        eligible_labels = get_speakers_eligible_for_llm_renaming(speakers)
        meeting_context = _resolve_meeting_event_context(session, recording)

        # Fetch user settings for provider resolution.
        user_settings = {}
        if recording.user_id:
            user = session.get(User, recording.user_id)
            if user and user.settings:
                user_settings = user.settings
        llm_config = resolve_llm_config(session, user_settings)
        missing_llm_config = llm_config.missing_configuration_message()

        suggestion_count = 0
        rule_based_result = SpeakerInferenceResult()
        if missing_llm_config:
            rule_based_result = detect_rule_based_speaker_suggestions(
                segments,
                eligible_labels,
                meeting_context,
            )
            suggestion_count += _persist_generated_speaker_name_suggestions(
                session,
                recording=recording,
                transcript=transcript,
                speakers=speakers,
                inference_result=rule_based_result,
                origin="manual_retry",
                provider=None,
                replaced_reason="manual_retry_refresh",
            )
            logger.warning(
                "Cannot infer speakers for recording %s: %s",
                recording_id,
                missing_llm_config,
            )
            if suggestion_count:
                session.commit()
                record_pipeline_metric(
                    stage="speaker_name_suggestions_generated",
                    recording_id=recording_id,
                    payload={
                        "origin": "manual_retry",
                        "suggestion_count": suggestion_count,
                        "rule_based_count": len(rule_based_result.suggestions),
                        "llm_count": 0,
                    },
                    log=logger,
                )
            _complete_speaker_inference_task(session, recording)
            return

        # Update status (optional, but good for UI feedback if we had a specific status for this)
        # For now, we just log it.
        logger.info(f"Starting independent speaker inference for recording {recording_id}")

        llm_result = SpeakerInferenceResult()
        if eligible_labels:
            transcript_for_llm = ""
            for seg in segments:
                start = seg.get("start", 0)
                end = seg.get("end", 0)

                def fmt(ts):
                    h = int(ts // 3600)
                    m = int((ts % 3600) // 60)
                    s = ts % 60
                    return f"{h:02}.{m:02}.{s:05.2f}s"

                diarization_label = seg.get("speaker", "Unknown")
                text = seg.get("text", "")
                transcript_for_llm += (
                    f"[{fmt(start)} - {fmt(end)}] - {diarization_label} - {text}\n"
                )

            backend = _llm_backend_from_config(llm_config)
            llm_result = backend.infer_speaker_suggestions(
                transcript_for_llm,
                user_notes=transcript.user_notes,
                meeting_context=meeting_context,
                eligible_labels=eligible_labels,
            )
            suggestion_count += _persist_generated_speaker_name_suggestions(
                session,
                recording=recording,
                transcript=transcript,
                speakers=speakers,
                inference_result=llm_result,
                origin="manual_retry",
                provider=llm_config.provider,
                replaced_reason="manual_retry_refresh",
            )
            superseded_count = _supersede_pending_speaker_name_suggestions_for_labels(
                session,
                transcript=transcript,
                diarization_labels=(
                    label for label in eligible_labels if label not in llm_result.mapping
                ),
                reason="manual_retry_omitted_by_llm",
            )
        else:
            superseded_count = 0

        session.commit()
        record_pipeline_metric(
            stage="speaker_name_suggestions_generated",
            recording_id=recording_id,
            payload={
                "origin": "manual_retry",
                "suggestion_count": suggestion_count,
                "superseded_count": superseded_count,
                "rule_based_count": len(rule_based_result.suggestions),
                "llm_count": len(llm_result.suggestions),
            },
            log=logger,
        )
        logger.info(
            "Stored %s speaker suggestions for recording %s",
            suggestion_count,
            recording_id,
        )

        _complete_speaker_inference_task(session, recording)

    except Exception as e:
        logger.error(f"Speaker inference task failed: {e}", exc_info=True)
        # Revert status to PROCESSED on error so spinner stops
        try:
            recording = session.get(Recording, recording_id)
            _complete_speaker_inference_task(session, recording)
        except Exception as db_err:  # noqa: BLE001
            logger.error(f"Failed to revert recording status: {db_err}")


@celery_app.task(name="backend.worker.tasks.process_document_task", base=DatabaseTask, bind=True)
def process_document_task(self, document_id: int):
    """
    Process an uploaded document: chunk text, embed, and store context chunks.
    """
    session = self.session
    document = session.get(Document, document_id)
    if not document:
        logger.error(f"Document {document_id} not found.")
        return

    try:
        document.status = DocumentStatus.PROCESSING
        session.add(document)
        session.commit()

        # Read file content
        content = ""
        if document.file_path.endswith(".txt") or document.file_path.endswith(".md"):
            with open(document.file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        elif document.file_path.endswith(".pdf"):
            import fitz # PyMuPDF
            try:
                doc = fitz.open(document.file_path)
                for page in doc:
                    content += page.get_text() + "\n\n"
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to extract text from PDF {document.file_path}: {e}")
                raise Exception(f"PDF extraction failed: {str(e)}")
        
        if not content:
            logger.warning(f"File content empty or unsupported type: {document.file_path}")
            pass

        # Chunking Strategy (Simple overlapping sliding window)
        CHUNK_SIZE = 500 # characters
        OVERLAP = 50
        
        chunks = []
        if content:
            start = 0
            while start < len(content):
                end = start + CHUNK_SIZE
                chunk_text = content[start:end]
                chunks.append(chunk_text)
                start += (CHUNK_SIZE - OVERLAP)
        
        if not chunks:
             logger.warning(f"No chunks generated for document {document_id}")
             document.status = DocumentStatus.READY
             session.add(document)
             session.commit()
             return

        # Embed chunks
        embedding_service = get_text_embedding_service()
        vectors = embedding_service.embed(chunks)
        
        # Store Chunks
        for i, (text_chunk, vector) in enumerate(zip(chunks, vectors)):
            db_chunk = ContextChunk(
                recording_id=document.recording_id,
                document_id=document.id,
                content=text_chunk,
                embedding=vector,
                meta={"chunk_index": i, "source": "document"}
            )
            session.add(db_chunk)
        
        document.status = DocumentStatus.READY
        session.add(document)
        session.commit()
        logger.info(f"Processed document {document_id}: {len(chunks)} chunks created.")

    except Exception as e:
        logger.error(f"Failed to process document {document_id}: {e}", exc_info=True)
        document.status = DocumentStatus.ERROR
        document.error_message = str(e)
        session.add(document)
        session.commit()


@celery_app.task(name="backend.worker.tasks.index_transcript_task", base=DatabaseTask, bind=True)
def index_transcript_task(self, recording_id: int):
    """
    Index the transcript of a completed recording for RAG.
    """
    session = self.session
    recording = session.get(Recording, recording_id)
    if not recording:
        return

    transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    segments = build_transcript_segments_for_read(
        session,
        recording_id,
        transcript=transcript,
    )
    if not transcript or not segments:
        return

    speakers = session.exec(select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)).all()
    speaker_map = build_recording_speaker_map(speakers)

    try:
        # Clear existing transcript chunks for this recording
        # The 'source' metadata field identifies these chunks.
        
        # Selects then deletes context chunks.
        existing_chunks = session.exec(
            select(ContextChunk)
            .where(ContextChunk.recording_id == recording_id)
            .where(ContextChunk.document_id == None).where(ContextChunk.meta['source'].as_string() == '"transcript"') 
            # Using document_id == None serves as a proxy for non-document chunks.
        ).all()
        
        for chunk in existing_chunks:
            if chunk.meta.get('source') == 'transcript':
                session.delete(chunk)
        
        # Chunks the transcript segments.
        # Grouping small segments improves embedding quality.
        
        segments = [dict(segment) for segment in segments]
        
        temp_chunk_text = ""
        temp_chunk_start = 0
        temp_chunk_end = 0
        temp_meta_speakers = set()
        
        chunks_to_embed = []
        metas = []
        
        current_length = 0
        TARGET_LENGTH = 1000 # chars
        
        for seg in segments:
            text = seg['text']
            start = seg['start']
            end = seg['end']
            speaker_label = seg['speaker']
            speaker_name = speaker_map.get(speaker_label, speaker_label)
            
            if current_length == 0:
                temp_chunk_start = start
            
            temp_chunk_text += f"{speaker_name}: {text}\n"
            current_length += len(text)
            temp_meta_speakers.add(speaker_name)
            temp_chunk_end = end
            
            if current_length >= TARGET_LENGTH:
                chunks_to_embed.append(temp_chunk_text)
                metas.append({
                    "start": temp_chunk_start,
                    "end": temp_chunk_end,
                    "speakers": list(temp_meta_speakers),
                    "source": "transcript"
                })
                
                # Reset
                temp_chunk_text = ""
                current_length = 0
                temp_meta_speakers = set()
                
        # Add remaining
        if temp_chunk_text:
             chunks_to_embed.append(temp_chunk_text)
             metas.append({
                "start": temp_chunk_start,
                "end": temp_chunk_end,
                "speakers": list(temp_meta_speakers),
                "source": "transcript"
            })
            
        if not chunks_to_embed:
            return

        embedding_service = get_text_embedding_service()
        vectors = embedding_service.embed(chunks_to_embed)
        
        for text, meta, vector in zip(chunks_to_embed, metas, vectors):
            db_chunk = ContextChunk(
                recording_id=recording_id,
                content=text,
                embedding=vector,
                meta=meta
            )
            session.add(db_chunk)
            
        session.commit()
        logger.info(f"Indexed transcript for recording {recording_id}: {len(chunks_to_embed)} chunks.")

    except Exception as e:
        logger.error(f"Failed to index transcript {recording_id}: {e}", exc_info=True)



def _format_notes_generation_error(error: Exception | str) -> str:
    message = str(error).strip() or "Meeting notes could not be generated."
    if len(message) > 500:
        message = f"{message[:497]}..."
    return message



def _mark_notes_generation_error_impl(
    session,
    recording: Recording | None,
    transcript: Transcript | None,
    error: Exception | str,
) -> None:
    if not transcript:
        return

    transcript.notes_status = "error"
    transcript.error_message = _format_notes_generation_error(error)
    session.add(transcript)

    if recording:
        recording.processing_step = "Error generating notes"
        session.add(recording)

    session.commit()

    if recording:
        update_recording_status(session, recording.id)



def _complete_speaker_inference_task(
    session,
    recording: Recording | None,
) -> None:
    if not recording:
        return

    recording.status = RecordingStatus.PROCESSED
    recording.client_status = ClientStatus.IDLE
    recording.processing_step = "Completed"
    session.add(recording)
    session.commit()



def _build_exact_global_speaker_name_map(
    session,
    *,
    user_id: int | None,
    suggested_names: Sequence[str],
) -> dict[str, int]:
    cleaned_names = sorted({name.strip() for name in suggested_names if str(name).strip()})
    if not user_id or not cleaned_names:
        return {}

    bind = session.get_bind()
    if bind is not None and not inspect(bind).has_table("global_speakers"):
        logger.debug(
            "Skipping exact global speaker matching for recording suggestions because the global_speakers table is unavailable.",
        )
        return {}

    global_speakers = session.exec(
        select(GlobalSpeaker)
        .where(GlobalSpeaker.user_id == user_id)
        .where(GlobalSpeaker.name.in_(cleaned_names))
    ).all()
    return {
        str(speaker.name).strip(): int(speaker.id)
        for speaker in global_speakers
        if speaker.id is not None and speaker.name
    }



def _build_persisted_speaker_name_suggestions(
    session,
    *,
    recording: Recording,
    speakers: Sequence[RecordingSpeaker],
    inference_result: SpeakerInferenceResult,
    origin: str,
    provider: str | None,
) -> list[dict[str, object]]:
    speakers_by_label = {speaker.diarization_label: speaker for speaker in speakers}
    exact_global_name_map = _build_exact_global_speaker_name_map(
        session,
        user_id=recording.user_id,
        suggested_names=[
            suggestion.suggested_name for suggestion in inference_result.suggestions
        ],
    )

    persisted: list[dict[str, object]] = []
    for suggestion in inference_result.suggestions:
        speaker = speakers_by_label.get(suggestion.diarization_label)
        if speaker is None:
            continue
        if speaker.merged_into_id or speaker.local_name or speaker.global_speaker_id:
            logger.info(
                "Skipping speaker suggestion for trusted or merged label %s",
                suggestion.diarization_label,
            )
            continue

        persisted.append(
            build_persisted_speaker_suggestion(
                suggestion,
                origin=origin,
                provider=provider,
                recording_speaker_id=speaker.id,
                suggested_global_speaker_id=exact_global_name_map.get(
                    suggestion.suggested_name
                ),
            )
        )

    return persisted



def _persist_generated_speaker_name_suggestions_impl(
    session,
    *,
    recording: Recording,
    transcript: Transcript,
    speakers: Sequence[RecordingSpeaker],
    inference_result: SpeakerInferenceResult,
    origin: str,
    provider: str | None,
    replaced_reason: str,
) -> int:
    if not inference_result.suggestions:
        return 0

    persisted = _build_persisted_speaker_name_suggestions(
        session,
        recording=recording,
        speakers=speakers,
        inference_result=inference_result,
        origin=origin,
        provider=provider,
    )
    if not persisted:
        return 0

    persist_transcript_speaker_suggestions(
        transcript,
        persisted,
        replaced_reason=replaced_reason,
    )
    flag_modified(transcript, "speaker_name_suggestions")
    session.add(transcript)
    return len(persisted)



def _supersede_pending_speaker_name_suggestions_for_labels_impl(
    session,
    *,
    transcript: Transcript,
    diarization_labels: Iterable[str],
    reason: str,
) -> int:
    superseded = supersede_pending_transcript_speaker_suggestions(
        transcript,
        diarization_labels=diarization_labels,
        reason=reason,
    )
    if not superseded:
        return 0
    flag_modified(transcript, "speaker_name_suggestions")
    session.add(transcript)
    return len(superseded)




__all__ = [name for name in globals() if not name.startswith('__')]
