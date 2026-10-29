"""Final reconciliation of canonical utterances and manual speaker inheritance."""

from .assignment import _resolve_recording_speaker_for_value
from .constants import *
from .preservation import preserve_human_segments, protected_final_payload
from .projection_segments import _build_projection_segment
from .public_ids import UtterancePublicIds
from .queries import (
    _processing_run_idempotency_key,
    ensure_processing_run,
    list_active_utterances,
)
from .scope import _lock_active_lines
from .segmentation import *
from .speaker import *


def _clear_pending_label(payload: dict[str, Any]) -> None:
    """Retain legacy confidence evidence while removing live presentation state."""
    label = payload.get("live_speaker_label")
    if isinstance(label, dict) and "diarisation_pending" in label:
        payload["live_speaker_label"] = {
            key: value for key, value in label.items() if key != "diarisation_pending"
        }


def _final_confidence_payload(
    segment: dict[str, Any],
    existing: dict[str, Any] | None = None,
    *,
    matched: bool = False,
) -> dict[str, Any] | None:
    """Keep evidence while replacing the previous run's alignment outcome."""
    payload = dict(existing or {})
    _clear_pending_label(payload)
    incoming = segment.get("confidence_payload")
    forced = segment.get("alignment_error") or any(
        word.get("source_id") for word in segment.get("words") or []
    )
    if isinstance(incoming, dict):
        forced = (
            forced
            or incoming.get("alignment_error")
            or any(
                word.get("source_id")
                for source in incoming.get("asr_segments") or []
                for word in source.get("words") or []
            )
        )
    if matched and not forced and not payload.get("alignment_error"):
        return payload if existing is not None else None
    if isinstance(incoming, dict) and (not matched or forced):
        payload.update(incoming)
    _clear_pending_label(payload)
    if forced and (segment.get("words") or segment.get("alignment_error")):
        from backend.processing.live_asr_payload import _build_live_confidence_payload

        # Canonical utterances store word evidence in the existing ASR payload;
        # rejected spans stay null, including after projection refreshes.
        evidence = _build_live_confidence_payload(
            region_segment_payloads=[segment],
            region_start_ms=0,
            region_end_ms=round(segment["end"] * 1000),
        )
        # Final segment/word seconds are already recording-relative: zero is
        # the conversion offset, not the utterance's actual start.
        evidence["utterance_start_ms"] = round(segment["start"] * 1000)
        payload.update(evidence)
    if segment.get("alignment_error"):
        payload["alignment_error"] = segment["alignment_error"]
    else:
        payload.pop("alignment_error", None)
    return payload if payload or isinstance(incoming, dict) else None


def _settled_final_confidence_payload(
    segment: dict[str, Any],
    existing: dict[str, Any] | None = None,
    *,
    matched: bool = False,
) -> dict[str, Any] | None:
    """Replace superseded word evidence for a recording with settled provenance."""
    from backend.processing.live_asr_payload import _build_live_confidence_payload

    if (
        segment.get("text_manually_edited") is True
        or (
            isinstance(segment.get("confidence_payload"), dict)
            and segment["confidence_payload"].get("human_protection")
        )
        or (matched and isinstance(existing, dict) and existing.get("human_protection"))
    ):
        return protected_final_payload(segment, existing)
    payload = dict(existing or {})
    incoming = segment.get("confidence_payload")
    if isinstance(incoming, dict):
        payload.update(incoming)
    payload.pop("live_text_settling", None)
    payload.pop("live_speaker_label", None)
    if (
        segment.get("words")
        or segment.get("alignment_error")
        or not (
            isinstance(incoming, dict)
            and incoming.get("asr_segments")
            and not isinstance(incoming.get("live_text_settling"), dict)
        )
    ):
        evidence = _build_live_confidence_payload(
            region_segment_payloads=[segment],
            region_start_ms=0,
            region_end_ms=round(segment["end"] * 1000),
        )
        evidence["utterance_start_ms"] = round(segment["start"] * 1000)
        payload.update(evidence)
    if segment.get("alignment_error"):
        payload["alignment_error"] = segment["alignment_error"]
    else:
        payload.pop("alignment_error", None)
    return payload or None


def _settled_segments(
    transcript: Transcript,
    lines: Sequence[TranscriptUtterance],
    segments: Sequence[dict[str, Any]],
) -> Sequence[dict[str, Any]]:
    """A recording with settled provenance keeps its human edits through the final run."""
    if (transcript.settling_state or {}).get("settled") is True:
        return preserve_human_segments(lines, segments)
    return segments


def finalize_utterances_from_segments(
    session,
    *,
    recording_id: int,
    segments: Sequence[dict[str, Any]],
    reused_live_asr: bool = False,
    trigger_source: str = "system",
    idempotency_key: str | None = None,
) -> list[TranscriptUtterance]:
    transcript = _load_transcript(session, recording_id, lock=True)
    recording = session.get(Recording, recording_id)
    if transcript is None or recording is None:
        return []
    final_confidence_payload = (
        _settled_final_confidence_payload
        if (transcript.settling_state or {}).get("settled") is True
        else _final_confidence_payload
    )
    active_utterances = sorted(
        _lock_active_lines(session, recording_id),
        key=lambda utterance: (utterance.sort_key, utterance.id),
    )

    run_idempotency_key = idempotency_key or _processing_run_idempotency_key(
        run_kind=ProcessingRunKind.FINALIZE,
        source="finalize",
        segments=segments,
        state_override=TranscriptUtteranceState.FINALIZED,
        reused_live_asr=reused_live_asr,
    )
    existing_processing_run = session.execute(
        select(ProcessingRun)
        .where(ProcessingRun.recording_id == recording_id)
        .where(ProcessingRun.idempotency_key == run_idempotency_key)
    ).scalar_one_or_none()
    if existing_processing_run is not None:
        return list_active_utterances(session, recording_id)

    processing_run = ensure_processing_run(
        session,
        recording_id=recording_id,
        run_kind=ProcessingRunKind.FINALIZE,
        trigger_source=trigger_source,
        reused_live_asr=reused_live_asr,
        span_start_ms=_segment_to_ms(
            min((segment.get("start", 0.0) for segment in segments), default=0.0)
        ),
        span_end_ms=_segment_to_ms(
            max((segment.get("end", 0.0) for segment in segments), default=0.0)
        ),
        idempotency_key=run_idempotency_key,
    )

    segments = _settled_segments(transcript, active_utterances, segments)

    recording_speakers = ensure_recording_speaker_aliases(
        session,
        recording_id,
        source_run_id=processing_run.id,
    )
    exact_matches: dict[tuple[int, int], list[TranscriptUtterance]] = defaultdict(list)
    for utterance in active_utterances:
        exact_matches[(utterance.start_ms, utterance.end_ms)].append(utterance)

    overlap_groups = _build_overlap_groups(segments)
    projection_segments: list[dict[str, Any]] = []
    utterances: list[TranscriptUtterance] = []
    new_boundary_utterances: list[TranscriptUtterance] = []
    matched_utterance_ids: set[int] = set()
    utterance_ids = UtterancePublicIds(
        session, recording_id=recording_id, segments=segments
    )

    def inherit_manual_speaker_for_range(
        *,
        start_ms: int,
        end_ms: int,
    ) -> dict[str, Any] | None:
        overlap_by_speaker: dict[int | str, int] = defaultdict(int)
        source_public_ids_by_speaker: dict[int | str, list[str]] = defaultdict(list)
        confidence_by_speaker: dict[int | str, float] = defaultdict(float)
        edit_sources_by_speaker: dict[int | str, set[str]] = defaultdict(set)

        for source_utterance in active_utterances:
            if source_utterance.id in matched_utterance_ids:
                continue
            if not source_utterance.manual_speaker_locked or (
                source_utterance.recording_speaker_id is None
                and live_speaker_display_name(source_utterance.speaker_label) is None
            ):
                continue
            overlap_ms = _range_overlap_ms(
                start_ms,
                end_ms,
                source_utterance.start_ms,
                source_utterance.end_ms,
            )
            if overlap_ms <= 0:
                continue
            speaker_key = source_utterance.recording_speaker_id or str(
                source_utterance.speaker_label
            )
            overlap_by_speaker[speaker_key] += int(overlap_ms)
            source_public_ids_by_speaker[speaker_key].append(source_utterance.public_id)
            if source_utterance.speaker_confidence is not None:
                confidence_by_speaker[speaker_key] = max(
                    confidence_by_speaker[speaker_key],
                    float(source_utterance.speaker_confidence),
                )
            if source_utterance.speaker_last_edit_source:
                edit_sources_by_speaker[speaker_key].add(
                    source_utterance.speaker_last_edit_source
                )

        if not overlap_by_speaker:
            return None

        ranked = sorted(
            overlap_by_speaker.items(),
            key=lambda item: (-item[1], str(item[0])),
        )
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return None

        speaker_key, overlap_ms = ranked[0]
        speaker = (
            session.get(RecordingSpeaker, speaker_key)
            if isinstance(speaker_key, int)
            else None
        )
        if isinstance(speaker_key, int) and speaker is None:
            return None

        # Attribution is only inherited when every utterance that voted for
        # this speaker was edited by the same surface. Where a person and an
        # assistant both touched the range, neither can honestly claim the
        # inherited assignment, so it goes across unattributed.
        edit_sources = edit_sources_by_speaker.get(speaker_key) or set()
        inherited_edit_source = (
            next(iter(edit_sources)) if len(edit_sources) == 1 else None
        )

        return {
            "speaker": speaker,
            "speaker_label": speaker.diarization_label
            if speaker is not None
            else str(speaker_key),
            "overlap_ms": int(overlap_ms),
            "source_public_ids": source_public_ids_by_speaker[speaker_key],
            "speaker_confidence": confidence_by_speaker.get(speaker_key) or None,
            "speaker_edit_source": inherited_edit_source,
        }

    for index, segment in enumerate(segments):
        start_ms = _segment_to_ms(segment.get("start", 0.0))
        end_ms = _segment_to_ms(segment.get("end", 0.0))
        boundary_key = (start_ms, end_ms)
        matched_utterance = None
        while exact_matches.get(boundary_key):
            candidate = exact_matches[boundary_key].pop(0)
            if candidate.id not in matched_utterance_ids:
                matched_utterance = candidate
                break

        manual_speaker_inheritance = None
        if matched_utterance is None:
            manual_speaker_inheritance = inherit_manual_speaker_for_range(
                start_ms=start_ms,
                end_ms=end_ms,
            )

        resolved_speaker = None
        if manual_speaker_inheritance is None and (
            matched_utterance is None or not matched_utterance.manual_speaker_locked
        ):
            resolved_speaker = _resolve_recording_speaker_for_value(
                session,
                recording_id=recording_id,
                recording=recording,
                speaker_value=str(segment.get("speaker") or UNKNOWN_SPEAKER),
                recording_speakers=recording_speakers,
                source_run_id=processing_run.id,
                source_segment=segment,
            )
            if resolved_speaker is not None:
                _touch_recording_speaker_bounds(resolved_speaker, segment)
                session.add(resolved_speaker)

        if matched_utterance is not None:
            matched_utterance_ids.add(matched_utterance.id)
            old_values = {
                "start_ms": matched_utterance.start_ms,
                "end_ms": matched_utterance.end_ms,
                "text": matched_utterance.text,
                "speaker": matched_utterance.speaker_label,
                "state": matched_utterance.state.value
                if hasattr(matched_utterance.state, "value")
                else str(matched_utterance.state),
                "revision": matched_utterance.revision,
            }
            old_text_locked = bool(matched_utterance.manual_text_locked)
            old_speaker_locked = bool(matched_utterance.manual_speaker_locked)
            protected_wording = bool(
                (transcript.settling_state or {}).get("settled") is True
                and (matched_utterance.confidence_payload or {}).get("human_protection")
            )

            effective_segment = dict(segment)
            effective_segment["segment_source"] = matched_utterance.source_kind or str(
                segment.get("segment_source") or "finalize"
            )
            effective_segment["text"] = (
                matched_utterance.text
                if old_text_locked or protected_wording
                else str(segment.get("text", "") or "")
            )
            effective_segment["text_manually_edited"] = old_text_locked or bool(
                segment.get("text_manually_edited") is True
            )
            # The lock is what pins the value, so the same condition pins its
            # provenance: a locked utterance keeps the source it was edited
            # with, and an unlocked one takes whatever the run supplies.
            effective_segment["text_edit_source"] = (
                matched_utterance.text_last_edit_source
                if old_text_locked
                else segment.get("text_edit_source")
            )

            if old_speaker_locked:
                current_speaker = (
                    session.get(
                        RecordingSpeaker, matched_utterance.recording_speaker_id
                    )
                    if matched_utterance.recording_speaker_id is not None
                    else None
                )
                effective_segment["speaker"] = (
                    current_speaker.diarization_label
                    if current_speaker is not None
                    else str(matched_utterance.speaker_label or UNKNOWN_SPEAKER)
                )
                recording_speaker = current_speaker
            else:
                recording_speaker = resolved_speaker
                effective_segment["speaker"] = (
                    recording_speaker.diarization_label
                    if recording_speaker is not None
                    else str(segment.get("speaker") or UNKNOWN_SPEAKER)
                )
            effective_segment["speaker_manually_edited"] = old_speaker_locked or bool(
                segment.get("speaker_manually_edited") is True
            )
            effective_segment["speaker_edit_source"] = (
                matched_utterance.speaker_last_edit_source
                if old_speaker_locked
                else segment.get("speaker_edit_source")
            )

            effective_text = str(effective_segment.get("text", "") or "")
            effective_speaker_label = (
                recording_speaker.diarization_label
                if recording_speaker is not None
                else str(effective_segment.get("speaker") or UNKNOWN_SPEAKER)
            )
            effective_recording_speaker_id = (
                recording_speaker.id if recording_speaker is not None else None
            )
            effective_manual_text_locked = bool(
                effective_segment.get("text_manually_edited") is True
            )
            effective_manual_speaker_locked = bool(
                effective_segment.get("speaker_manually_edited") is True
            )
            effective_text_edit_source = _segment_edit_source(
                effective_segment,
                "text_edit_source",
                locked=effective_manual_text_locked,
            )
            effective_speaker_edit_source = _segment_edit_source(
                effective_segment,
                "speaker_edit_source",
                locked=effective_manual_speaker_locked,
            )
            effective_text_confidence = _to_optional_float(
                segment.get("text_confidence")
            )
            if effective_text_confidence is None:
                effective_text_confidence = matched_utterance.text_confidence
            effective_speaker_confidence = _to_optional_float(
                segment.get("speaker_confidence")
            )
            if effective_speaker_confidence is None:
                effective_speaker_confidence = matched_utterance.speaker_confidence
            effective_confidence_payload = final_confidence_payload(
                effective_segment,
                matched_utterance.confidence_payload,
                matched=True,
            )
            effective_speaker_assignment_source = (
                _resolve_segment_speaker_assignment_source(
                    effective_segment,
                    source="finalize",
                    state=TranscriptUtteranceState.FINALIZED,
                    manual_speaker_locked=effective_manual_speaker_locked,
                )
            )
            effective_speaker_assignment_authority = (
                _resolve_segment_speaker_assignment_authority(
                    effective_segment,
                    state=TranscriptUtteranceState.FINALIZED,
                    manual_speaker_locked=effective_manual_speaker_locked,
                )
            )

            changed = any(
                (
                    matched_utterance.sort_key != _sort_key_for_index(index),
                    matched_utterance.text != effective_text,
                    matched_utterance.speaker_label != effective_speaker_label,
                    matched_utterance.recording_speaker_id
                    != effective_recording_speaker_id,
                    bool(matched_utterance.manual_text_locked)
                    != effective_manual_text_locked,
                    bool(matched_utterance.manual_speaker_locked)
                    != effective_manual_speaker_locked,
                    matched_utterance.text_last_edit_source
                    != effective_text_edit_source,
                    matched_utterance.speaker_last_edit_source
                    != effective_speaker_edit_source,
                    matched_utterance.state != TranscriptUtteranceState.FINALIZED,
                    matched_utterance.processing_run_id != processing_run.id,
                    matched_utterance.overlap_group_id
                    != overlap_groups.get(index, {}).get("group_id"),
                    int(matched_utterance.overlap_rank or 0)
                    != overlap_groups.get(index, {}).get("rank", 0),
                    matched_utterance.text_confidence != effective_text_confidence,
                    matched_utterance.speaker_confidence
                    != effective_speaker_confidence,
                    matched_utterance.confidence_payload
                    != effective_confidence_payload,
                    _utterance_speaker_assignment_source(matched_utterance)
                    != effective_speaker_assignment_source,
                    _utterance_speaker_assignment_authority(matched_utterance)
                    != effective_speaker_assignment_authority,
                )
            )

            matched_utterance.sort_key = _sort_key_for_index(index)
            matched_utterance.text = effective_text
            matched_utterance.speaker_label = effective_speaker_label
            matched_utterance.recording_speaker_id = effective_recording_speaker_id
            matched_utterance.manual_text_locked = effective_manual_text_locked
            matched_utterance.manual_speaker_locked = effective_manual_speaker_locked
            matched_utterance.text_last_edit_source = effective_text_edit_source
            matched_utterance.speaker_last_edit_source = effective_speaker_edit_source
            matched_utterance.text_confidence = effective_text_confidence
            matched_utterance.speaker_confidence = effective_speaker_confidence
            matched_utterance.confidence_payload = effective_confidence_payload
            matched_utterance.state = TranscriptUtteranceState.FINALIZED
            matched_utterance.processing_run_id = processing_run.id
            matched_utterance.overlap_group_id = overlap_groups.get(index, {}).get(
                "group_id"
            )
            matched_utterance.overlap_rank = overlap_groups.get(index, {}).get(
                "rank", 0
            )
            matched_utterance.speaker_assignment_source = (
                effective_speaker_assignment_source
            )
            matched_utterance.speaker_assignment_authority = (
                effective_speaker_assignment_authority
            )
            if changed:
                matched_utterance.revision += 1
                session.add(matched_utterance)
                session.flush()
                _append_utterance_event(
                    session,
                    utterance=matched_utterance,
                    processing_run_id=processing_run.id,
                    event_type="finalize",
                    source="finalize",
                    old_values=old_values,
                    new_values={
                        "start_ms": matched_utterance.start_ms,
                        "end_ms": matched_utterance.end_ms,
                        "text": matched_utterance.text,
                        "speaker": matched_utterance.speaker_label,
                        "state": TranscriptUtteranceState.FINALIZED.value,
                    },
                    resulting_revision=matched_utterance.revision,
                )
                _record_manual_lock_events(
                    session,
                    utterance=matched_utterance,
                    old_text_locked=old_text_locked,
                    old_speaker_locked=old_speaker_locked,
                    source="finalize",
                    processing_run_id=processing_run.id,
                )
            utterances.append(matched_utterance)
            effective_segment["revision"] = matched_utterance.revision
            projection_segments.append(
                _build_projection_segment(
                    matched_utterance,
                    source_segment=effective_segment,
                    recording_speaker=recording_speaker,
                    overlap_labels=_projection_overlap_labels(
                        index, segments, overlap_groups, recording_speakers
                    ),
                )
            )
            continue

        effective_segment = dict(segment)
        if manual_speaker_inheritance is not None:
            recording_speaker = manual_speaker_inheritance["speaker"]
            effective_segment["speaker"] = manual_speaker_inheritance["speaker_label"]
            effective_segment["recording_speaker_id"] = (
                recording_speaker.id if recording_speaker is not None else None
            )
            effective_segment["speaker_manually_edited"] = True
            effective_segment["speaker_edit_source"] = manual_speaker_inheritance.get(
                "speaker_edit_source"
            )
            if effective_segment.get("speaker_confidence") is None:
                effective_segment["speaker_confidence"] = (
                    manual_speaker_inheritance.get("speaker_confidence")
                )
            confidence_payload = dict(effective_segment.get("confidence_payload") or {})
            confidence_payload["inherited_manual_speaker"] = {
                "recording_speaker_id": effective_segment["recording_speaker_id"],
                "source_public_ids": list(
                    manual_speaker_inheritance["source_public_ids"]
                ),
                "overlap_ms": int(manual_speaker_inheritance["overlap_ms"]),
            }
            effective_segment["confidence_payload"] = confidence_payload
            if recording_speaker is not None:
                _touch_recording_speaker_bounds(recording_speaker, effective_segment)
                session.add(recording_speaker)
        else:
            recording_speaker = resolved_speaker
        utterance = TranscriptUtterance(
            public_id=utterance_ids.assign(effective_segment),
            recording_id=recording_id,
            sort_key=_sort_key_for_index(index),
            start_ms=start_ms,
            end_ms=end_ms,
            text=str(effective_segment.get("text", "") or ""),
            speaker_label=(
                recording_speaker.diarization_label
                if recording_speaker
                else str(effective_segment.get("speaker") or UNKNOWN_SPEAKER)
            ),
            recording_speaker_id=recording_speaker.id if recording_speaker else None,
            diarization_label=_segment_diarization_label(effective_segment),
            state=TranscriptUtteranceState.FINALIZED,
            source_kind=str(effective_segment.get("segment_source") or "finalize"),
            processing_run_id=processing_run.id,
            revision=int(effective_segment.get("revision") or 1),
            overlap_group_id=overlap_groups.get(index, {}).get("group_id"),
            overlap_rank=overlap_groups.get(index, {}).get("rank", 0),
            manual_text_locked=bool(
                effective_segment.get("text_manually_edited") is True
            ),
            manual_speaker_locked=bool(
                effective_segment.get("speaker_manually_edited") is True
            ),
            text_last_edit_source=_segment_edit_source(
                effective_segment,
                "text_edit_source",
                locked=bool(effective_segment.get("text_manually_edited") is True),
            ),
            speaker_last_edit_source=_segment_edit_source(
                effective_segment,
                "speaker_edit_source",
                locked=bool(effective_segment.get("speaker_manually_edited") is True),
            ),
            speaker_assignment_source=_resolve_segment_speaker_assignment_source(
                effective_segment,
                source="finalize",
                state=TranscriptUtteranceState.FINALIZED,
                manual_speaker_locked=bool(
                    effective_segment.get("speaker_manually_edited") is True
                ),
            ),
            speaker_assignment_authority=_resolve_segment_speaker_assignment_authority(
                effective_segment,
                state=TranscriptUtteranceState.FINALIZED,
                manual_speaker_locked=bool(
                    effective_segment.get("speaker_manually_edited") is True
                ),
            ),
            text_confidence=_to_optional_float(
                effective_segment.get("text_confidence")
            ),
            speaker_confidence=_to_optional_float(
                effective_segment.get("speaker_confidence")
            ),
            confidence_payload=final_confidence_payload(effective_segment),
        )
        session.add(utterance)
        session.flush()
        new_values = {
            "start_ms": utterance.start_ms,
            "end_ms": utterance.end_ms,
            "text": utterance.text,
            "speaker": utterance.speaker_label,
            "state": TranscriptUtteranceState.FINALIZED.value,
        }
        if manual_speaker_inheritance is not None:
            new_values["inherited_manual_speaker_from_public_ids"] = list(
                manual_speaker_inheritance["source_public_ids"]
            )
        _append_utterance_event(
            session,
            utterance=utterance,
            processing_run_id=processing_run.id,
            event_type="finalize",
            source="finalize",
            old_values=None,
            new_values=new_values,
            resulting_revision=utterance.revision,
        )
        _record_manual_lock_events(
            session,
            utterance=utterance,
            old_text_locked=False,
            old_speaker_locked=False,
            source="finalize",
            processing_run_id=processing_run.id,
        )
        utterances.append(utterance)
        new_boundary_utterances.append(utterance)
        projection_segments.append(
            _build_projection_segment(
                utterance,
                source_segment=effective_segment,
                recording_speaker=recording_speaker,
                overlap_labels=_projection_overlap_labels(
                    index, segments, overlap_groups, recording_speakers
                ),
            )
        )

    previous_boundary_utterances: list[TranscriptUtterance] = []
    for utterance in active_utterances:
        if utterance.id in matched_utterance_ids:
            continue
        previous_boundary_utterances.append(utterance)
        old_state = (
            utterance.state.value
            if hasattr(utterance.state, "value")
            else str(utterance.state)
        )
        utterance.state = TranscriptUtteranceState.SUPERSEDED
        session.add(utterance)
        session.flush()
        _append_utterance_event(
            session,
            utterance=utterance,
            processing_run_id=processing_run.id,
            event_type="supersede",
            source="finalize",
            old_values={"state": old_state},
            new_values={"state": TranscriptUtteranceState.SUPERSEDED.value},
            resulting_revision=utterance.revision,
        )

    transcript.segments = projection_segments
    transcript.text = " ".join(
        segment.get("text", "") for segment in projection_segments
    ).strip()
    flag_modified(transcript, "segments")
    session.add(transcript)

    _append_boundary_revision_events(
        session,
        previous_utterances=previous_boundary_utterances,
        new_utterances=new_boundary_utterances,
        processing_run_id=processing_run.id,
        source="finalize",
    )

    refresh_recording_speaker_usage_state(session, recording_id)

    return utterances


__all__ = [
    "finalize_utterances_from_segments",
]
