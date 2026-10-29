"""Automatic meeting-intelligence stage (notes, title, speaker suggestions).

Extracted from backend.worker.tasks.pipeline as a pure decomposition. The shared
surface comes from .constants (the same import hub the other task submodules use),
so the constants.py shim wrappers keep resolving the ``*_impl`` functions here via
the ``backend.worker.tasks`` package namespace with no call-site changes.
"""

import dataclasses
import logging

from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.utils.context_window import ContextWindowExceededError
from backend.utils.live_speaker_labels import speaker_display_name
from backend.utils.meeting_notes import append_user_notes_section
from backend.utils.timeline_notes import render_timeline_for_llm
from backend.utils.transcript_change import change_sequence_bump
from backend.worker.tasks.constants import *  # noqa: F401,F403 -- shared task surface
from backend.worker.tasks.notes_outputs import (
    generate_automatic_glossary,
    prepare_automatic_glossary,
)

logger = logging.getLogger(__name__)


def _format_recording_timestamp(seconds: float) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(max(float(seconds), 0.0)))


def _build_automatic_meeting_intelligence_transcript_impl(
    segments: Sequence[dict],
    speaker_map: dict[str, str],
    unresolved_speakers: Sequence[str],
    timeline_notes: RecordingTimelineNotes,
) -> str:
    """The transcript for the unified prompt, with the body notes on their own lines."""
    unresolved_labels = set(unresolved_speakers)

    def format_line(seg: dict) -> str:
        speaker_label = str(seg.get("speaker", "Unknown"))
        display_name = (
            speaker_label
            if speaker_label in unresolved_labels
            else speaker_display_name(speaker_label, speaker_map)
        )

        overlapping_names = []
        for overlapping_label in seg.get("overlapping_speakers", []):
            normalized_label = str(overlapping_label)
            if normalized_label in unresolved_labels:
                overlapping_names.append(normalized_label)
            else:
                overlapping_names.append(
                    speaker_display_name(normalized_label, speaker_map)
                )

        overlapping_suffix = (
            f" (with {', '.join(overlapping_names)})" if overlapping_names else ""
        )
        text = str(seg.get("text", "")).strip()
        return (
            f"[{_format_recording_timestamp(seg.get('start', 0))} - "
            f"{_format_recording_timestamp(seg.get('end', seg.get('start', 0)))}] "
            f"{display_name}{overlapping_suffix}: {text}"
        )

    segment_list = list(segments)
    if not segment_list:
        # No transcript: notes alone are no meeting to summarise, so the stage
        # still sees an empty transcript and skips the model.
        return ""
    return render_timeline_for_llm(
        segment_list,
        timeline_notes.place(segment_list),
        format_segment=format_line,
        author_names=timeline_notes.author_names,
        format_time=lambda ms: _format_recording_timestamp(ms / 1000),
    )


def _apply_automatic_meeting_intelligence_result(
    session,
    recording: Recording,
    transcript: Transcript,
    speakers: Sequence[RecordingSpeaker],
    result: AutomaticMeetingIntelligenceResult,
    *,
    meeting_context: MeetingEventContext | None,
    provider: str | None,
    resolved_template: ResolvedNotesTemplate | None = None,
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
    # Freshly generated notes reflect every READY document by definition.
    transcript.notes_stale_documents = False
    transcript.set_notes_error_message(None)
    if resolved_template is not None:
        # Provenance: the template and its text at generation time (issue #137).
        transcript.notes_template_id = resolved_template.template_id
        transcript.notes_template_sections = resolved_template.sections
    session.add(recording)
    session.add(transcript)
    # As in generate_notes_task: the notes status can be back where another
    # tab last saw it.
    session.execute(change_sequence_bump(recording.id))
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
    timeline_notes: RecordingTimelineNotes,
    detected_transcription_language: str | None = None,
    update_processing_status: bool = True,
) -> AutomaticMeetingIntelligenceResult | None:
    # update_processing_status is False when this runs as a deferred IO task: the
    # recording has already been marked Completed by the GPU pipeline, so we must
    # not reset its processing_step/progress back to the "Generating Notes" stage.
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
            session.execute(change_sequence_bump(recording.id))
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
            session.execute(change_sequence_bump(recording.id))
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
        apply_recording_language(llm_config.merged_config, recording),
        transcription_backend=llm_config.merged_config.get("transcription_backend"),
        detected_transcription_language=detected_transcription_language,
    )
    notes_context, resolved_template = build_notes_prompt_context(
        session,
        recording=recording,
        speakers=speakers,
        settings=llm_config.merged_config,
        user_id=recording.user_id,
    )
    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript=cleaned_transcript,
        unresolved_speakers=tuple(unresolved_speakers),
        user_notes=timeline_notes.user_notes_context(),
        prefer_short_titles=prefer_short_titles,
        meeting_context=meeting_context,
        output_language_instruction=language_preferences.notes_language_instruction,
        notes_sections=notes_context.notes_sections,
        glossary=notes_context.glossary,
        meeting_metadata=notes_context.metadata,
        documents=notes_context.documents,
    )

    if update_processing_status:
        if task is not None:
            task.update_state(
                state="PROCESSING",
                meta={
                    "progress": AUTOMATIC_MEETING_INTELLIGENCE_PROGRESS,
                    "stage": AUTOMATIC_MEETING_INTELLIGENCE_STAGE,
                },
            )
        recording.processing_step = (
            f"{AUTOMATIC_MEETING_INTELLIGENCE_STEP}{device_suffix}"
        )
        recording.processing_progress = AUTOMATIC_MEETING_INTELLIGENCE_PROGRESS
        session.add(recording)

    transcript.notes_status = "generating"
    transcript.set_notes_error_message(None)
    session.add(transcript)
    session.commit()
    update_recording_status(session, recording.id)

    try:
        llm = _llm_backend_from_config(llm_config)
        try:
            result = llm.generate_meeting_intelligence(
                request,
                timeout=AUTOMATIC_MEETING_INTELLIGENCE_TIMEOUT_SECONDS,
            )
        except ContextWindowExceededError as overflow:
            # Reached only when the secondary provider, if any, also failed on
            # the whole meeting. See backend.utils.long_meeting_notes.
            logger.info(
                "Recording %s is too long for one call; generating its notes "
                "in parts: %s",
                recording.id,
                overflow,
            )
            result = llm.generate_meeting_intelligence_in_parts(
                request,
                overflow=overflow,
                timeout=AUTOMATIC_MEETING_INTELLIGENCE_TIMEOUT_SECONDS,
            )
        result = dataclasses.replace(
            result,
            notes_markdown=append_user_notes_section(
                result.notes_markdown, timeline_notes.appendix_lines()
            ),
        )
        glossary_output = prepare_automatic_glossary(session, recording)
        _apply_automatic_meeting_intelligence_result(
            session,
            recording,
            transcript,
            speakers,
            result,
            meeting_context=meeting_context,
            provider=llm_config.provider,
            resolved_template=resolved_template,
        )
        generate_automatic_glossary(session, recording, glossary_output)
        logger.info(
            "Generated unified meeting intelligence for recording %s",
            recording.id,
        )
        return result
    except Exception as exc:  # noqa: BLE001 -- boundary: any failure is recorded on the transcript as a notes error; the recording still completes
        logger.error(
            "Failed to generate automatic meeting intelligence for recording %s: %s",
            recording.id,
            exc,
        )
        _mark_notes_generation_error(session, recording, transcript, exc)
        return None


__all__ = [name for name in globals() if not name.startswith("__")]
