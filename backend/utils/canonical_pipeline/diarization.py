from .constants import *
from .segmentation import *
from .speaker import *


def _build_split_replacement_segments_from_diarization(
    utterance: TranscriptUtterance,
    *,
    turn_rows: Sequence[DiarizationWindowTurn],
    recording_speakers_by_id: dict[int, RecordingSpeaker],
    window_result_id: int,
) -> list[dict[str, Any]]:
    if utterance.manual_text_locked or utterance.manual_speaker_locked:
        return []

    utterance_words = _load_utterance_asr_words(utterance)
    if len(utterance_words) < 2:
        return []

    # First pass: best-effort speaker per word from overlapping turns. Words
    # that have no overlapping matched turn (typical at silence-aligned
    # speaker boundaries) are left as None and filled below from the nearest
    # mapped neighbour so a single unmapped word does not defeat the split.
    per_word_speaker_ids: list[int | None] = []
    for word_payload in utterance_words:
        speaker_id = _match_word_to_recording_speaker_id(
            word_payload,
            turn_rows=turn_rows,
        )
        if speaker_id is not None and speaker_id not in recording_speakers_by_id:
            speaker_id = None
        per_word_speaker_ids.append(speaker_id)

    if all(speaker_id is None for speaker_id in per_word_speaker_ids):
        return []

    # Forward-fill then backward-fill so every word inherits the nearest
    # mapped neighbour's speaker.
    last_known: int | None = None
    for index, speaker_id in enumerate(per_word_speaker_ids):
        if speaker_id is None:
            per_word_speaker_ids[index] = last_known
        else:
            last_known = speaker_id
    last_known = None
    for index in range(len(per_word_speaker_ids) - 1, -1, -1):
        if per_word_speaker_ids[index] is None:
            per_word_speaker_ids[index] = last_known
        else:
            last_known = per_word_speaker_ids[index]

    if any(speaker_id is None for speaker_id in per_word_speaker_ids):
        return []

    speaker_groups: list[dict[str, Any]] = []
    for word_payload, speaker_id in zip(utterance_words, per_word_speaker_ids):
        if speaker_id is None or speaker_id not in recording_speakers_by_id:
            return []
        if speaker_groups and int(speaker_groups[-1]["speaker_id"]) == int(speaker_id):
            speaker_groups[-1]["words"].append(word_payload)
            continue
        speaker_groups.append(
            {
                "speaker_id": int(speaker_id),
                "words": [word_payload],
            }
        )

    if len(speaker_groups) < 2:
        return []
    if len({int(group["speaker_id"]) for group in speaker_groups}) < 2:
        return []

    base_payload = dict(utterance.confidence_payload or {})
    base_rolling_payload = dict(base_payload.get("rolling_diarization") or {})
    replacement_segments: list[dict[str, Any]] = []

    for group in speaker_groups:
        words = list(group["words"])
        group_text = _join_asr_words(words)
        if not group_text:
            return []

        group_start_ms = max(int(utterance.start_ms), int(words[0]["start_ms"]))
        group_end_ms = min(int(utterance.end_ms), int(words[-1]["end_ms"]))
        if group_end_ms <= group_start_ms:
            return []

        recording_speaker = recording_speakers_by_id[int(group["speaker_id"])]
        rolling_payload = dict(base_rolling_payload)
        rolling_payload.update(
            {
                "window_result_id": int(window_result_id),
                "matched_recording_speaker_id": int(recording_speaker.id),
                "split_from_public_id": utterance.public_id,
            }
        )
        replacement_payload = dict(base_payload)
        replacement_payload["rolling_diarization"] = rolling_payload
        replacement_payload["asr_segments"] = [
            {
                "start_ms": int(group_start_ms),
                "end_ms": int(group_end_ms),
                "text": group_text,
                "words": [dict(word_payload) for word_payload in words],
            }
        ]
        replacement_payload["asr_word_timestamps_available"] = True

        replacement_segments.append(
            {
                "id": str(uuid4()),
                "start": group_start_ms / 1000.0,
                "end": group_end_ms / 1000.0,
                "text": group_text,
                "speaker": recording_speaker.diarization_label,
                "recording_speaker_id": int(recording_speaker.id),
                "segment_source": utterance.source_kind,
                "provisional": utterance.state == TranscriptUtteranceState.PROVISIONAL,
                "state": utterance.state.value
                if hasattr(utterance.state, "value")
                else str(utterance.state),
                "speaker_manually_edited": False,
                "text_manually_edited": False,
                "speaker_confidence": utterance.speaker_confidence,
                "text_confidence": utterance.text_confidence,
                "confidence_payload": replacement_payload,
                "last_diarization_window_result_id": int(window_result_id),
            }
        )

    return replacement_segments


def _build_turn_boundary_split_segments_from_diarization(
    utterance: TranscriptUtterance,
    *,
    turn_rows: Sequence[DiarizationWindowTurn],
    recording_speakers_by_id: dict[int, RecordingSpeaker],
    window_result_id: int,
) -> list[dict[str, Any]]:
    """Split a transition utterance at diarization boundaries when the
    word-level splitter cannot run (e.g. ASR returned no word timestamps).

    Triggers when two or more matched recording speakers each cover a
    meaningful, contiguous slice of the utterance. Text is distributed
    proportionally by word count (whitespace tokens) along the boundary.
    """

    if utterance.manual_text_locked or utterance.manual_speaker_locked:
        return []

    utterance_start_ms = int(utterance.start_ms)
    utterance_end_ms = int(utterance.end_ms)
    utterance_duration_ms = utterance_end_ms - utterance_start_ms
    if utterance_duration_ms <= 0:
        return []

    # Collect overlapping turns clipped to the utterance and grouped into
    # consecutive same-speaker runs, in time order.
    clipped_turns: list[tuple[int, int, int]] = []
    for turn_row in turn_rows:
        if turn_row.matched_recording_speaker_id is None:
            continue
        speaker_id = int(turn_row.matched_recording_speaker_id)
        if speaker_id not in recording_speakers_by_id:
            continue
        start_ms = max(utterance_start_ms, int(turn_row.start_ms))
        end_ms = min(utterance_end_ms, int(turn_row.end_ms))
        if end_ms <= start_ms:
            continue
        clipped_turns.append((start_ms, end_ms, speaker_id))

    if len(clipped_turns) < 2:
        return []

    clipped_turns.sort(key=lambda item: (item[0], item[1]))

    runs: list[dict[str, int]] = []
    for start_ms, end_ms, speaker_id in clipped_turns:
        if (
            runs
            and int(runs[-1]["speaker_id"]) == speaker_id
            and start_ms <= int(runs[-1]["end_ms"])
        ):
            runs[-1]["end_ms"] = max(int(runs[-1]["end_ms"]), end_ms)
            continue
        runs.append({"start_ms": start_ms, "end_ms": end_ms, "speaker_id": speaker_id})

    if len(runs) < 2:
        return []

    distinct_speaker_ids = {int(run["speaker_id"]) for run in runs}
    if len(distinct_speaker_ids) < 2:
        return []

    overlap_by_speaker_id: dict[int, int] = defaultdict(int)
    for run in runs:
        overlap_by_speaker_id[int(run["speaker_id"])] += int(run["end_ms"]) - int(
            run["start_ms"]
        )

    min_overlap_ms = max(
        ROLLING_DIARIZATION_BOUNDARY_SPLIT_MIN_OVERLAP_MS,
        int(
            round(ROLLING_DIARIZATION_BOUNDARY_SPLIT_MIN_RATIO * utterance_duration_ms)
        ),
    )
    qualifying_speaker_ids = {
        speaker_id
        for speaker_id, overlap_ms in overlap_by_speaker_id.items()
        if overlap_ms >= min_overlap_ms
    }
    if len(qualifying_speaker_ids) < 2:
        return []

    # Merge any small runs whose speaker did not qualify into the previous
    # qualifying run so we keep contiguous, meaningful boundaries.
    coalesced_runs: list[dict[str, int]] = []
    for run in runs:
        if int(run["speaker_id"]) in qualifying_speaker_ids:
            if coalesced_runs and int(coalesced_runs[-1]["speaker_id"]) == int(
                run["speaker_id"]
            ):
                coalesced_runs[-1]["end_ms"] = max(
                    int(coalesced_runs[-1]["end_ms"]), int(run["end_ms"])
                )
                continue
            coalesced_runs.append(dict(run))
        else:
            if not coalesced_runs:
                continue
            coalesced_runs[-1]["end_ms"] = max(
                int(coalesced_runs[-1]["end_ms"]), int(run["end_ms"])
            )

    if len({int(run["speaker_id"]) for run in coalesced_runs}) < 2:
        return []

    # Close gaps so the union covers the full utterance: snap each boundary
    # to the midpoint of any silence gap between consecutive runs.
    for index in range(len(coalesced_runs) - 1):
        left = coalesced_runs[index]
        right = coalesced_runs[index + 1]
        if int(right["start_ms"]) > int(left["end_ms"]):
            midpoint = (int(left["end_ms"]) + int(right["start_ms"])) // 2
            left["end_ms"] = midpoint
            right["start_ms"] = midpoint
        elif int(right["start_ms"]) < int(left["end_ms"]):
            # Overlapping runs: split at midpoint of the overlap.
            midpoint = (int(left["end_ms"]) + int(right["start_ms"])) // 2
            left["end_ms"] = midpoint
            right["start_ms"] = midpoint
    coalesced_runs[0]["start_ms"] = utterance_start_ms
    coalesced_runs[-1]["end_ms"] = utterance_end_ms

    # Distribute text across the runs. Prefer word timestamps when available
    # so the split aligns with actual spoken words; otherwise fall back to a
    # whitespace-token proportional split.
    utterance_words = _load_utterance_asr_words(utterance)
    text_by_run: list[str] = []
    if utterance_words:
        text_by_run = _split_text_by_word_timestamps(coalesced_runs, utterance_words)
    if not text_by_run or any(not chunk.strip() for chunk in text_by_run):
        text_by_run = _split_text_proportionally(
            str(utterance.text or ""), coalesced_runs
        )
    if not text_by_run or len(text_by_run) != len(coalesced_runs):
        return []
    if any(not chunk.strip() for chunk in text_by_run):
        return []

    base_payload = dict(utterance.confidence_payload or {})
    base_rolling_payload = dict(base_payload.get("rolling_diarization") or {})
    replacement_segments: list[dict[str, Any]] = []

    for run, run_text in zip(coalesced_runs, text_by_run):
        speaker_id = int(run["speaker_id"])
        recording_speaker = recording_speakers_by_id.get(speaker_id)
        if recording_speaker is None:
            return []
        run_start_ms = int(run["start_ms"])
        run_end_ms = int(run["end_ms"])
        if run_end_ms <= run_start_ms:
            return []

        rolling_payload = dict(base_rolling_payload)
        rolling_payload.update(
            {
                "window_result_id": int(window_result_id),
                "matched_recording_speaker_id": int(recording_speaker.id),
                "split_from_public_id": utterance.public_id,
                "split_strategy": "diarization_turn_boundary",
            }
        )
        replacement_payload = dict(base_payload)
        replacement_payload["rolling_diarization"] = rolling_payload
        replacement_payload["asr_segments"] = [
            {
                "start_ms": run_start_ms,
                "end_ms": run_end_ms,
                "text": run_text.strip(),
            }
        ]
        replacement_payload["asr_word_timestamps_available"] = False

        replacement_segments.append(
            {
                "id": str(uuid4()),
                "start": run_start_ms / 1000.0,
                "end": run_end_ms / 1000.0,
                "text": run_text.strip(),
                "speaker": recording_speaker.diarization_label,
                "recording_speaker_id": int(recording_speaker.id),
                "segment_source": utterance.source_kind,
                "provisional": utterance.state == TranscriptUtteranceState.PROVISIONAL,
                "state": utterance.state.value
                if hasattr(utterance.state, "value")
                else str(utterance.state),
                "speaker_manually_edited": False,
                "text_manually_edited": False,
                "speaker_confidence": utterance.speaker_confidence,
                "text_confidence": utterance.text_confidence,
                "confidence_payload": replacement_payload,
                "last_diarization_window_result_id": int(window_result_id),
            }
        )

    return replacement_segments


def _split_text_by_word_timestamps(
    runs: Sequence[dict[str, int]],
    utterance_words: Sequence[dict[str, Any]],
) -> list[str]:
    grouped: list[list[str]] = [[] for _ in runs]
    for word_payload in utterance_words:
        word_start_ms = int(word_payload["start_ms"])
        word_end_ms = int(word_payload["end_ms"])
        word_text = str(word_payload.get("word") or "").strip()
        if not word_text:
            continue
        best_index = 0
        best_overlap = -1
        for index, run in enumerate(runs):
            overlap_ms = _range_overlap_ms(
                word_start_ms,
                word_end_ms,
                int(run["start_ms"]),
                int(run["end_ms"]),
            )
            if overlap_ms > best_overlap:
                best_overlap = overlap_ms
                best_index = index
        grouped[best_index].append(word_text)
    return [" ".join(words) for words in grouped]


def _split_text_proportionally(
    text: str,
    runs: Sequence[dict[str, int]],
) -> list[str]:
    tokens = (text or "").split()
    if not tokens:
        return []
    total_duration_ms = sum(int(run["end_ms"]) - int(run["start_ms"]) for run in runs)
    if total_duration_ms <= 0:
        return []
    total_tokens = len(tokens)
    chunks: list[str] = []
    cursor = 0
    for index, run in enumerate(runs):
        if index == len(runs) - 1:
            chunk_tokens = tokens[cursor:]
        else:
            run_duration_ms = int(run["end_ms"]) - int(run["start_ms"])
            take = max(
                1,
                int(round(total_tokens * (run_duration_ms / total_duration_ms))),
            )
            take = min(take, total_tokens - cursor - (len(runs) - index - 1))
            take = max(1, take)
            chunk_tokens = tokens[cursor : cursor + take]
            cursor += take
        chunks.append(" ".join(chunk_tokens))
    if any(not chunk.strip() for chunk in chunks):
        return []
    return chunks


def _apply_boundary_reconciliation_segments(
    session,
    *,
    recording_id: int,
    source_utterances: Sequence[TranscriptUtterance],
    replacement_segments: Sequence[dict[str, Any]],
    processing_run_id: int | None,
    source: str,
) -> list[TranscriptUtterance]:
    from .core import (
        _build_projection_segment,
        _projection_overlap_labels,
        _state_for_segment,
        list_active_utterances,
    )

    transcript = _load_transcript(session, recording_id)
    recording = session.get(Recording, recording_id)
    if (
        transcript is None
        or recording is None
        or not source_utterances
        or not replacement_segments
    ):
        return []

    active_utterances = list_active_utterances(session, recording_id)
    source_utterance_ids = {
        int(utterance.id) for utterance in source_utterances if utterance.id is not None
    }
    if not source_utterance_ids:
        return []

    recording_speakers = ensure_recording_speaker_aliases(
        session,
        recording_id,
        source_run_id=processing_run_id,
    )
    recording_speakers_by_id = {
        int(speaker.id): speaker
        for speaker in recording_speakers
        if speaker.id is not None
    }

    source_inserted = False
    resulting_segments: list[dict[str, Any]] = []
    ordered_active_utterances = sorted(
        active_utterances,
        key=lambda utterance: (utterance.sort_key, int(utterance.id or 0)),
    )
    for active_utterance in ordered_active_utterances:
        if int(active_utterance.id or 0) in source_utterance_ids:
            if not source_inserted:
                resulting_segments.extend(
                    dict(segment) for segment in replacement_segments
                )
                source_inserted = True
            continue
        resulting_segments.append(_segment_payload_from_utterance(active_utterance))

    if not source_inserted:
        return []

    overlap_groups = _build_overlap_groups(resulting_segments)
    replacement_utterances: list[TranscriptUtterance] = []
    projection_segments: list[dict[str, Any]] = []
    remaining_existing_utterances = {
        utterance.public_id: utterance
        for utterance in ordered_active_utterances
        if int(utterance.id or 0) not in source_utterance_ids
    }

    for source_utterance in source_utterances:
        old_state = (
            source_utterance.state.value
            if hasattr(source_utterance.state, "value")
            else str(source_utterance.state)
        )
        source_utterance.state = TranscriptUtteranceState.SUPERSEDED
        session.add(source_utterance)
        session.flush()
        _append_utterance_event(
            session,
            utterance=source_utterance,
            processing_run_id=processing_run_id,
            event_type="supersede",
            source=source,
            old_values={"state": old_state},
            new_values={"state": TranscriptUtteranceState.SUPERSEDED.value},
            resulting_revision=source_utterance.revision,
        )

    for index, segment in enumerate(resulting_segments):
        overlap_group = overlap_groups.get(index, {})
        existing_utterance = remaining_existing_utterances.get(
            str(segment.get("id") or "")
        )
        if existing_utterance is not None:
            existing_utterance.sort_key = _sort_key_for_index(index)
            existing_utterance.overlap_group_id = overlap_group.get("group_id")
            existing_utterance.overlap_rank = overlap_group.get("rank", 0)
            session.add(existing_utterance)
            recording_speaker = (
                recording_speakers_by_id.get(
                    int(existing_utterance.recording_speaker_id)
                )
                if existing_utterance.recording_speaker_id is not None
                else None
            )
            projection_segments.append(
                _build_projection_segment(
                    existing_utterance,
                    source_segment=segment,
                    recording_speaker=recording_speaker,
                    overlap_labels=_projection_overlap_labels(
                        index,
                        resulting_segments,
                        overlap_groups,
                        recording_speakers,
                    ),
                )
            )
            continue

        recording_speaker = None
        recording_speaker_id = segment.get("recording_speaker_id")
        if recording_speaker_id is not None:
            recording_speaker = recording_speakers_by_id.get(int(recording_speaker_id))

        utterance = TranscriptUtterance(
            public_id=str(segment.get("id") or uuid4()),
            recording_id=recording_id,
            sort_key=_sort_key_for_index(index),
            start_ms=_segment_to_ms(segment.get("start", 0.0)),
            end_ms=_segment_to_ms(segment.get("end", 0.0)),
            text=str(segment.get("text", "") or ""),
            speaker_label=(
                recording_speaker.diarization_label
                if recording_speaker is not None
                else str(segment.get("speaker") or UNKNOWN_SPEAKER)
            ),
            recording_speaker_id=(
                recording_speaker.id if recording_speaker is not None else None
            ),
            state=_state_for_segment(recording, segment),
            source_kind=str(segment.get("segment_source") or source),
            processing_run_id=processing_run_id,
            revision=int(segment.get("revision") or 1),
            overlap_group_id=overlap_group.get("group_id"),
            overlap_rank=overlap_group.get("rank", 0),
            manual_text_locked=bool(segment.get("text_manually_edited") is True),
            manual_speaker_locked=bool(segment.get("speaker_manually_edited") is True),
            speaker_assignment_source=_resolve_segment_speaker_assignment_source(
                segment,
                source=source,
                state=_state_for_segment(recording, segment),
                manual_speaker_locked=bool(
                    segment.get("speaker_manually_edited") is True
                ),
            ),
            speaker_assignment_authority=_resolve_segment_speaker_assignment_authority(
                segment,
                state=_state_for_segment(recording, segment),
                manual_speaker_locked=bool(
                    segment.get("speaker_manually_edited") is True
                ),
            ),
            text_confidence=_to_optional_float(segment.get("text_confidence")),
            speaker_confidence=_to_optional_float(segment.get("speaker_confidence")),
            confidence_payload=(
                dict(segment.get("confidence_payload"))
                if isinstance(segment.get("confidence_payload"), dict)
                else None
            ),
            last_diarization_window_result_id=segment.get(
                "last_diarization_window_result_id"
            ),
        )
        session.add(utterance)
        session.flush()
        _append_utterance_event(
            session,
            utterance=utterance,
            processing_run_id=processing_run_id,
            event_type=_creation_event_type(source=source, state=utterance.state),
            source=source,
            old_values=None,
            new_values={
                "start_ms": utterance.start_ms,
                "end_ms": utterance.end_ms,
                "text": utterance.text,
                "speaker": utterance.speaker_label,
            },
            resulting_revision=utterance.revision,
        )
        replacement_utterances.append(utterance)
        projection_segments.append(
            _build_projection_segment(
                utterance,
                source_segment=segment,
                recording_speaker=recording_speaker,
                overlap_labels=_projection_overlap_labels(
                    index,
                    resulting_segments,
                    overlap_groups,
                    recording_speakers,
                ),
            )
        )

    transcript.segments = projection_segments
    transcript.text = " ".join(
        segment.get("text", "") for segment in projection_segments
    ).strip()
    flag_modified(transcript, "segments")
    session.add(transcript)
    refresh_recording_speaker_usage_state(session, recording_id)

    _append_boundary_revision_events(
        session,
        previous_utterances=source_utterances,
        new_utterances=replacement_utterances,
        processing_run_id=processing_run_id,
        source=source,
    )

    return replacement_utterances


__all__ = [name for name in globals() if not name.startswith("__")]
