from .constants import *


@dataclass(frozen=True)
class IdentityReplayScope:
    anchor_recording_speaker_id: int
    allowed_recording_speaker_ids: frozenset[int]


@dataclass(frozen=True)
class SpeakerReplayPolicy:
    name: str
    allow_speaker_reassignment: bool = True
    allow_new_recording_speakers: bool = True
    allow_inactive_candidate_reactivation: bool = True
    preserve_finalized_overlap_primary: bool = False
    preserve_finalized_boundary_primary: bool = False
    identity_scope: IdentityReplayScope | None = None


DEFAULT_SPEAKER_REPLAY_POLICY = SpeakerReplayPolicy(name="default")
BOUNDARY_ONLY_SPEAKER_REPLAY_POLICY = SpeakerReplayPolicy(
    name="boundary_only",
    allow_speaker_reassignment=False,
)


def _normalize_speaker_assignment_source(value: Any) -> str:
    normalized = str(value or "").strip() or SPEAKER_ASSIGNMENT_SOURCE_LEGACY
    if normalized == "speaker_identity_replay":
        return SPEAKER_ASSIGNMENT_SOURCE_IDENTITY_REPLAY
    return normalized


def _normalize_speaker_assignment_authority(value: Any) -> str:
    normalized = str(value or "").strip()
    if normalized in SPEAKER_ASSIGNMENT_AUTHORITY_RANK:
        return normalized
    return SPEAKER_ASSIGNMENT_AUTHORITY_PROVISIONAL


def _derive_default_speaker_assignment_authority(
    *,
    state: TranscriptUtteranceState | str | None,
    manual_speaker_locked: bool,
) -> str:
    if manual_speaker_locked:
        return SPEAKER_ASSIGNMENT_AUTHORITY_MANUAL
    state_value = state.value if hasattr(state, "value") else str(state or "")
    if state_value == TranscriptUtteranceState.FINALIZED.value:
        return SPEAKER_ASSIGNMENT_AUTHORITY_FINALIZED
    return SPEAKER_ASSIGNMENT_AUTHORITY_PROVISIONAL


def _derive_default_speaker_assignment_source(
    *,
    source: str | None,
    source_kind: str | None,
    state: TranscriptUtteranceState | str | None,
    manual_speaker_locked: bool,
) -> str:
    if manual_speaker_locked:
        return SPEAKER_ASSIGNMENT_SOURCE_MANUAL

    normalized_source = _normalize_speaker_assignment_source(source)
    state_value = state.value if hasattr(state, "value") else str(state or "")
    normalized_source_kind = _normalize_speaker_assignment_source(source_kind)

    if normalized_source == SPEAKER_ASSIGNMENT_SOURCE_IDENTITY_REPLAY:
        return SPEAKER_ASSIGNMENT_SOURCE_IDENTITY_REPLAY
    if normalized_source == SPEAKER_ASSIGNMENT_SOURCE_CORRECTION_REPLAY:
        return SPEAKER_ASSIGNMENT_SOURCE_CORRECTION_REPLAY
    if normalized_source == SPEAKER_ASSIGNMENT_SOURCE_MANUAL:
        return SPEAKER_ASSIGNMENT_SOURCE_MANUAL
    if (
        normalized_source == SPEAKER_ASSIGNMENT_SOURCE_FINALIZE
        or state_value == TranscriptUtteranceState.FINALIZED.value
    ):
        return SPEAKER_ASSIGNMENT_SOURCE_FINALIZE
    if normalized_source_kind in {
        SPEAKER_ASSIGNMENT_SOURCE_LIVE,
        SPEAKER_ASSIGNMENT_SOURCE_CATCH_UP,
        SPEAKER_ASSIGNMENT_SOURCE_BACKFILL,
        SPEAKER_ASSIGNMENT_SOURCE_COMPATIBILITY_REPLACE,
    }:
        return normalized_source_kind
    if normalized_source in {
        SPEAKER_ASSIGNMENT_SOURCE_LIVE,
        SPEAKER_ASSIGNMENT_SOURCE_CATCH_UP,
        SPEAKER_ASSIGNMENT_SOURCE_BACKFILL,
        SPEAKER_ASSIGNMENT_SOURCE_COMPATIBILITY_REPLACE,
    }:
        return normalized_source
    if (
        normalized_source_kind
        and normalized_source_kind != SPEAKER_ASSIGNMENT_SOURCE_LEGACY
    ):
        return normalized_source_kind
    if normalized_source and normalized_source != SPEAKER_ASSIGNMENT_SOURCE_LEGACY:
        return normalized_source
    return SPEAKER_ASSIGNMENT_SOURCE_LEGACY


def _max_speaker_assignment_authority(*values: Any) -> str:
    strongest = SPEAKER_ASSIGNMENT_AUTHORITY_PROVISIONAL
    strongest_rank = SPEAKER_ASSIGNMENT_AUTHORITY_RANK[strongest]
    for value in values:
        normalized = _normalize_speaker_assignment_authority(value)
        rank = SPEAKER_ASSIGNMENT_AUTHORITY_RANK[normalized]
        if rank > strongest_rank:
            strongest = normalized
            strongest_rank = rank
    return strongest


def _resolve_segment_speaker_assignment_source(
    segment: dict[str, Any],
    *,
    source: str,
    state: TranscriptUtteranceState | str | None,
    manual_speaker_locked: bool,
) -> str:
    explicit = segment.get("speaker_assignment_source")
    source_kind = str(segment.get("segment_source") or source)
    if explicit:
        return _normalize_speaker_assignment_source(explicit)
    return _derive_default_speaker_assignment_source(
        source=source,
        source_kind=source_kind,
        state=state,
        manual_speaker_locked=manual_speaker_locked,
    )


def _resolve_segment_speaker_assignment_authority(
    segment: dict[str, Any],
    *,
    state: TranscriptUtteranceState | str | None,
    manual_speaker_locked: bool,
) -> str:
    explicit = segment.get("speaker_assignment_authority")
    if explicit:
        return _normalize_speaker_assignment_authority(explicit)
    return _derive_default_speaker_assignment_authority(
        state=state,
        manual_speaker_locked=manual_speaker_locked,
    )


def _utterance_speaker_assignment_source(
    utterance: TranscriptUtterance,
    *,
    projection: dict[str, Any] | None = None,
) -> str:
    projection = projection or {}
    current_value = getattr(
        utterance, "speaker_assignment_source", None
    ) or projection.get("speaker_assignment_source")
    if current_value:
        return _normalize_speaker_assignment_source(current_value)
    return _derive_default_speaker_assignment_source(
        source=str(getattr(utterance, "source_kind", "") or ""),
        source_kind=str(getattr(utterance, "source_kind", "") or ""),
        state=utterance.state,
        manual_speaker_locked=bool(utterance.manual_speaker_locked),
    )


def _utterance_speaker_assignment_authority(
    utterance: TranscriptUtterance,
    *,
    projection: dict[str, Any] | None = None,
) -> str:
    projection = projection or {}
    current_value = getattr(
        utterance, "speaker_assignment_authority", None
    ) or projection.get("speaker_assignment_authority")
    if current_value:
        return _normalize_speaker_assignment_authority(current_value)
    return _derive_default_speaker_assignment_authority(
        state=utterance.state,
        manual_speaker_locked=bool(utterance.manual_speaker_locked),
    )


def _set_utterance_speaker_assignment_provenance(
    utterance: TranscriptUtterance,
    *,
    source: str,
    authority: str | None = None,
) -> None:
    utterance.speaker_assignment_source = _normalize_speaker_assignment_source(source)
    utterance.speaker_assignment_authority = (
        _normalize_speaker_assignment_authority(authority)
        if authority is not None
        else _derive_default_speaker_assignment_authority(
            state=utterance.state,
            manual_speaker_locked=bool(utterance.manual_speaker_locked),
        )
    )


def _effective_speaker_replay_policy(
    replay_policy: SpeakerReplayPolicy | None,
    *,
    allow_speaker_reassignment: bool,
) -> SpeakerReplayPolicy:
    if replay_policy is not None:
        return replay_policy
    if allow_speaker_reassignment:
        return DEFAULT_SPEAKER_REPLAY_POLICY
    return BOUNDARY_ONLY_SPEAKER_REPLAY_POLICY


def _is_identity_replay_policy(replay_policy: SpeakerReplayPolicy | None) -> bool:
    return replay_policy is not None and replay_policy.identity_scope is not None


def _replay_source_for_policy(replay_policy: SpeakerReplayPolicy | None) -> str:
    if _is_identity_replay_policy(replay_policy):
        return "speaker_identity_replay"
    return "speaker_correction_replay"


def _replay_normalized_recording_speaker_id(
    replay_policy: SpeakerReplayPolicy | None,
    recording_speaker_id: int | None,
) -> int | None:
    if recording_speaker_id is None or not _is_identity_replay_policy(replay_policy):
        return recording_speaker_id
    if not _identity_replay_scope_contains(replay_policy, recording_speaker_id):
        return recording_speaker_id
    return _identity_replay_anchor_recording_speaker_id(replay_policy)


def _speaker_replay_policy_for_correction_event(
    session,
    *,
    recording_id: int,
    event_type: SpeakerCorrectionEventType,
    target_recording_speaker_id: int | None,
) -> SpeakerReplayPolicy | None:
    if event_type in {
        SpeakerCorrectionEventType.LINK_GLOBAL_SPEAKER,
        SpeakerCorrectionEventType.PROMOTE_GLOBAL_SPEAKER,
        SpeakerCorrectionEventType.MERGE_SPEAKERS,
    }:
        if target_recording_speaker_id is None:
            return SpeakerReplayPolicy(
                name="identity_guarded",
                allow_new_recording_speakers=False,
                allow_inactive_candidate_reactivation=False,
                preserve_finalized_overlap_primary=True,
                preserve_finalized_boundary_primary=True,
            )
        return _build_identity_replay_policy(
            session,
            recording_id=recording_id,
            anchor_recording_speaker_id=int(target_recording_speaker_id),
        )
    if event_type in {
        SpeakerCorrectionEventType.ASSIGN_RECORDING_SPEAKER,
        SpeakerCorrectionEventType.ASSIGN_FROM_NOW_ON,
    }:
        return DEFAULT_SPEAKER_REPLAY_POLICY
    return None


def _record_guarded_replay_rejection(
    *,
    recording_id: int,
    window_result_id: int,
    replay_policy: SpeakerReplayPolicy,
    reason: str,
    utterance: TranscriptUtterance,
    candidate_speaker_id: int | None,
    current_speaker_id: int | None,
) -> None:
    if not _is_identity_replay_policy(replay_policy):
        return
    record_pipeline_metric(
        stage="speaker_identity_replay_rejection",
        recording_id=recording_id,
        payload={
            "policy": replay_policy.name,
            "reason": reason,
            "window_result_id": int(window_result_id),
            "utterance_public_id": utterance.public_id,
            "utterance_state": (
                utterance.state.value
                if hasattr(utterance.state, "value")
                else str(utterance.state)
            ),
            "candidate_recording_speaker_id": candidate_speaker_id,
            "current_recording_speaker_id": current_speaker_id,
        },
        log=logger,
    )


def ensure_recording_speaker_aliases_for_speaker(
    session,
    recording_speaker: RecordingSpeaker,
    *,
    source_run_id: int | None = None,
) -> None:
    existing_rows = (
        session.execute(
            select(RecordingSpeakerAlias).where(
                RecordingSpeakerAlias.recording_speaker_id == recording_speaker.id
            )
        )
        .scalars()
        .all()
    )
    existing = {
        (
            row.alias_type.value
            if hasattr(row.alias_type, "value")
            else str(row.alias_type),
            row.alias_value,
        )
        for row in existing_rows
    }

    candidates: list[tuple[RecordingSpeakerAliasType, str]] = []
    if recording_speaker.diarization_label:
        candidates.append(
            (
                _alias_type_for_value(recording_speaker.diarization_label),
                recording_speaker.diarization_label,
            )
        )
    if recording_speaker.local_name:
        candidates.append(
            (RecordingSpeakerAliasType.DISPLAY_NAME, recording_speaker.local_name)
        )
    if (
        recording_speaker.name
        and recording_speaker.name != recording_speaker.local_name
    ):
        candidates.append(
            (RecordingSpeakerAliasType.DISPLAY_NAME, recording_speaker.name)
        )
    global_speaker = getattr(recording_speaker, "global_speaker", None)
    if global_speaker is None and recording_speaker.global_speaker_id:
        global_speaker = session.get(GlobalSpeaker, recording_speaker.global_speaker_id)
    if global_speaker is not None and getattr(global_speaker, "name", None):
        candidates.append((RecordingSpeakerAliasType.GLOBAL_NAME, global_speaker.name))

    for alias_type, alias_value in candidates:
        key = (alias_type.value, alias_value)
        if key in existing:
            continue
        _ensure_recording_speaker_alias(
            session,
            recording_speaker_id=recording_speaker.id,
            alias_type=alias_type,
            alias_value=alias_value,
            source_run_id=source_run_id,
            active=True,
        )


def _ensure_recording_speaker_alias(
    session,
    *,
    recording_speaker_id: int,
    alias_type: RecordingSpeakerAliasType,
    alias_value: str,
    source_run_id: int | None = None,
    active: bool = True,
    valid_from_ms: int | None = None,
    valid_to_ms: int | None = None,
    confidence: float | None = None,
) -> RecordingSpeakerAlias:
    existing_rows = (
        session.execute(
            select(RecordingSpeakerAlias)
            .where(RecordingSpeakerAlias.recording_speaker_id == recording_speaker_id)
            .where(RecordingSpeakerAlias.alias_type == alias_type)
            .where(RecordingSpeakerAlias.alias_value == alias_value)
        )
        .scalars()
        .all()
    )

    for row in existing_rows:
        if (
            bool(row.active) == bool(active)
            and row.valid_from_ms == valid_from_ms
            and row.valid_to_ms == valid_to_ms
        ):
            if source_run_id is not None and row.source_run_id is None:
                row.source_run_id = source_run_id
                session.add(row)
            if confidence is not None and row.confidence is None:
                row.confidence = confidence
                session.add(row)
            return row

    alias_row = RecordingSpeakerAlias(
        recording_speaker_id=recording_speaker_id,
        alias_type=alias_type,
        alias_value=alias_value,
        source_run_id=source_run_id,
        active=active,
        valid_from_ms=valid_from_ms,
        valid_to_ms=valid_to_ms,
        confidence=confidence,
    )
    session.add(alias_row)
    return alias_row


def ensure_recording_speaker_aliases(
    session,
    recording_id: int,
    *,
    source_run_id: int | None = None,
) -> list[RecordingSpeaker]:
    speakers = _load_recording_speakers(session, recording_id)
    for speaker in speakers:
        if speaker.merged_into_id:
            speaker.speaker_status = "merged"
        elif not speaker.speaker_status or speaker.speaker_status == "merged":
            speaker.speaker_status = "active"
        session.add(speaker)
        ensure_recording_speaker_aliases_for_speaker(
            session,
            speaker,
            source_run_id=source_run_id,
        )
    return speakers


def active_recording_speaker_ids_for_read(
    session, recording_id: int
) -> tuple[set[int], bool]:
    rows = session.execute(
        select(TranscriptUtterance.recording_speaker_id)
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(TranscriptUtterance.state.in_(ACTIVE_UTTERANCE_STATES))
        .where(TranscriptUtterance.recording_speaker_id.is_not(None))
    ).all()
    active_ids = {
        int(recording_speaker_id)
        for (recording_speaker_id,) in rows
        if recording_speaker_id is not None
    }

    has_active_utterances = bool(
        session.execute(
            select(TranscriptUtterance.id)
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(TranscriptUtterance.state.in_(ACTIVE_UTTERANCE_STATES))
            .limit(1)
        ).first()
    )
    return active_ids, has_active_utterances


def filter_recording_speakers_for_public_read(
    session,
    recording_id: int,
    speakers: Sequence[RecordingSpeaker],
) -> list[RecordingSpeaker]:
    active_speaker_ids, has_active_utterances = active_recording_speaker_ids_for_read(
        session,
        recording_id,
    )
    filtered: list[RecordingSpeaker] = []
    for speaker in speakers:
        if speaker.merged_into_id:
            continue
        if has_active_utterances and speaker.id not in active_speaker_ids:
            continue
        filtered.append(speaker)
    return filtered


def refresh_recording_speaker_usage_state(session, recording_id: int) -> None:
    active_speaker_ids, has_active_utterances = active_recording_speaker_ids_for_read(
        session,
        recording_id,
    )
    if not has_active_utterances:
        return

    for speaker in _load_recording_speakers(session, recording_id):
        next_status = "merged" if speaker.merged_into_id else "active"
        if not speaker.merged_into_id and speaker.id not in active_speaker_ids:
            next_status = "inactive"
        if speaker.speaker_status == next_status:
            continue
        speaker.speaker_status = next_status
        session.add(speaker)


def merge_recording_speaker_aliases(
    session,
    *,
    source_speaker: RecordingSpeaker,
    target_speaker: RecordingSpeaker,
    source_run_id: int | None = None,
) -> None:
    ensure_recording_speaker_aliases_for_speaker(
        session,
        source_speaker,
        source_run_id=source_run_id,
    )
    ensure_recording_speaker_aliases_for_speaker(
        session,
        target_speaker,
        source_run_id=source_run_id,
    )

    source_alias_rows = (
        session.execute(
            select(RecordingSpeakerAlias).where(
                RecordingSpeakerAlias.recording_speaker_id == source_speaker.id
            )
        )
        .scalars()
        .all()
    )
    target_alias_rows = (
        session.execute(
            select(RecordingSpeakerAlias).where(
                RecordingSpeakerAlias.recording_speaker_id == target_speaker.id
            )
        )
        .scalars()
        .all()
    )
    existing_target_keys = {
        (
            row.alias_type.value
            if hasattr(row.alias_type, "value")
            else str(row.alias_type),
            row.alias_value,
        )
        for row in target_alias_rows
    }

    for alias_row in source_alias_rows:
        alias_type_value = (
            alias_row.alias_type.value
            if hasattr(alias_row.alias_type, "value")
            else str(alias_row.alias_type)
        )
        alias_key = (alias_type_value, alias_row.alias_value)
        if alias_key in existing_target_keys:
            continue
        _ensure_recording_speaker_alias(
            session,
            recording_speaker_id=target_speaker.id,
            alias_type=alias_row.alias_type,
            alias_value=alias_row.alias_value,
            source_run_id=alias_row.source_run_id or source_run_id,
            active=bool(alias_row.active),
            valid_from_ms=alias_row.valid_from_ms,
            valid_to_ms=alias_row.valid_to_ms,
            confidence=alias_row.confidence,
        )
        existing_target_keys.add(alias_key)


def _generic_display_aliases_for_label(label: str | None) -> set[str]:
    if not label:
        return set()

    live_match = re.match(r"^LIVE_(\d+)$", label)
    if live_match:
        return {f"Speaker {int(live_match.group(1))}"}

    diarization_match = re.match(r"^SPEAKER_(\d+)$", label)
    if diarization_match:
        return {f"Speaker {int(diarization_match.group(1)) + 1}"}

    return set()


def _is_generic_speaker_display_alias(value: str | None) -> bool:
    return bool(value and GENERIC_SPEAKER_DISPLAY_PATTERN.match(value.strip()))


def _continuity_alias_values_for_speaker(
    session, recording_speaker: RecordingSpeaker
) -> set[str]:
    ensure_recording_speaker_aliases_for_speaker(session, recording_speaker)

    values: set[str] = set()
    if recording_speaker.diarization_label:
        values.add(recording_speaker.diarization_label)
        values.update(
            _generic_display_aliases_for_label(recording_speaker.diarization_label)
        )

    alias_rows = session.execute(
        select(RecordingSpeakerAlias.alias_type, RecordingSpeakerAlias.alias_value)
        .where(RecordingSpeakerAlias.recording_speaker_id == recording_speaker.id)
        .where(RecordingSpeakerAlias.active.is_(True))
    ).all()
    machine_alias_types = {
        RecordingSpeakerAliasType.DIARIZATION_LABEL.value,
        RecordingSpeakerAliasType.LIVE_LABEL.value,
        RecordingSpeakerAliasType.MANUAL_LABEL.value,
        RecordingSpeakerAliasType.IMPORT_LABEL.value,
    }
    for alias_type, alias_value in alias_rows:
        if not alias_value:
            continue
        alias_type_value = (
            alias_type.value if hasattr(alias_type, "value") else str(alias_type)
        )
        alias_text = str(alias_value)
        if alias_type_value in machine_alias_types or _is_generic_speaker_display_alias(
            alias_text
        ):
            values.add(alias_text)

    return values


def _preserve_speaker_label_continuity(
    session,
    *,
    source_speaker: RecordingSpeaker | None,
    target_speaker: RecordingSpeaker,
    scope: SpeakerCorrectionScope,
    anchor_start_ms: int,
) -> None:
    if source_speaker is None or source_speaker.id == target_speaker.id:
        return
    if scope in {
        SpeakerCorrectionScope.UTTERANCE_ONLY,
        SpeakerCorrectionScope.MERGE_INTO_SPEAKER,
    }:
        return

    valid_from_ms = (
        anchor_start_ms
        if scope == SpeakerCorrectionScope.FROM_THIS_UTTERANCE_FORWARD
        else None
    )
    for alias_value in _continuity_alias_values_for_speaker(session, source_speaker):
        alias_type = (
            RecordingSpeakerAliasType.DISPLAY_NAME
            if _is_generic_speaker_display_alias(alias_value)
            else _alias_type_for_value(alias_value)
        )
        _ensure_recording_speaker_alias(
            session,
            recording_speaker_id=target_speaker.id,
            alias_type=alias_type,
            alias_value=alias_value,
            source_run_id=target_speaker.processing_run_id
            or source_speaker.processing_run_id,
            active=True,
            valid_from_ms=valid_from_ms,
        )


def _resolve_active_recording_speaker(
    session,
    recording_speaker: RecordingSpeaker,
) -> RecordingSpeaker:
    current = recording_speaker
    visited: set[int] = set()
    while current.merged_into_id and current.id not in visited:
        visited.add(current.id)
        merged_target = session.get(RecordingSpeaker, current.merged_into_id)
        if merged_target is None:
            break
        current = merged_target
    return current


def _build_identity_replay_policy(
    session,
    *,
    recording_id: int,
    anchor_recording_speaker_id: int,
) -> SpeakerReplayPolicy:
    anchor_speaker = session.get(RecordingSpeaker, anchor_recording_speaker_id)
    if anchor_speaker is None or anchor_speaker.recording_id != recording_id:
        return SpeakerReplayPolicy(
            name="identity_guarded",
            allow_new_recording_speakers=False,
            allow_inactive_candidate_reactivation=False,
            preserve_finalized_overlap_primary=True,
            preserve_finalized_boundary_primary=True,
        )

    resolved_anchor = _resolve_active_recording_speaker(session, anchor_speaker)
    recording_speakers = _load_recording_speakers(session, recording_id)
    allowed_recording_speaker_ids: set[int] = {
        int(resolved_anchor.id),
        int(anchor_speaker.id),
    }

    for candidate_speaker in recording_speakers:
        candidate_id = getattr(candidate_speaker, "id", None)
        if candidate_id is None:
            continue
        resolved_candidate = _resolve_active_recording_speaker(
            session, candidate_speaker
        )
        if getattr(resolved_candidate, "id", None) == getattr(
            resolved_anchor, "id", None
        ):
            allowed_recording_speaker_ids.add(int(candidate_id))
            allowed_recording_speaker_ids.add(int(resolved_candidate.id))
            continue
        if (
            resolved_anchor.global_speaker_id is not None
            and candidate_speaker.global_speaker_id == resolved_anchor.global_speaker_id
        ):
            allowed_recording_speaker_ids.add(int(candidate_id))
            if getattr(resolved_candidate, "id", None) is not None:
                allowed_recording_speaker_ids.add(int(resolved_candidate.id))

    return SpeakerReplayPolicy(
        name="identity_guarded",
        allow_new_recording_speakers=False,
        allow_inactive_candidate_reactivation=False,
        preserve_finalized_overlap_primary=True,
        preserve_finalized_boundary_primary=True,
        identity_scope=IdentityReplayScope(
            anchor_recording_speaker_id=int(resolved_anchor.id),
            allowed_recording_speaker_ids=frozenset(allowed_recording_speaker_ids),
        ),
    )


def _identity_replay_scope_contains(
    replay_policy: SpeakerReplayPolicy,
    recording_speaker_id: int | None,
) -> bool:
    if replay_policy.identity_scope is None or recording_speaker_id is None:
        return False
    return (
        int(recording_speaker_id)
        in replay_policy.identity_scope.allowed_recording_speaker_ids
    )


def _identity_replay_anchor_recording_speaker_id(
    replay_policy: SpeakerReplayPolicy,
) -> int | None:
    if replay_policy.identity_scope is None:
        return None
    return int(replay_policy.identity_scope.anchor_recording_speaker_id)


def _normalized_identity_replay_candidate(
    session,
    *,
    replay_policy: SpeakerReplayPolicy,
    matched_speaker: RecordingSpeaker | None,
) -> RecordingSpeaker | None:
    if matched_speaker is None or replay_policy.identity_scope is None:
        return matched_speaker
    if not _identity_replay_scope_contains(
        replay_policy, getattr(matched_speaker, "id", None)
    ):
        return matched_speaker
    anchor_speaker = session.get(
        RecordingSpeaker,
        replay_policy.identity_scope.anchor_recording_speaker_id,
    )
    if anchor_speaker is None:
        return matched_speaker
    return _resolve_active_recording_speaker(session, anchor_speaker)


def _apply_source_run_provenance(
    session,
    recording_speaker: RecordingSpeaker,
    source_run_id: int | None,
) -> None:
    if source_run_id is None or recording_speaker.processing_run_id is not None:
        return
    recording_speaker.processing_run_id = source_run_id
    session.add(recording_speaker)


def _find_matching_recording_speaker(
    session,
    *,
    recording_id: int,
    recording_speakers: list[RecordingSpeaker],
    value: str,
    source_run_id: int | None,
    segment_start_ms: int | None = None,
) -> RecordingSpeaker | None:
    speaker_ids = [speaker.id for speaker in recording_speakers]
    if speaker_ids and segment_start_ms is not None:
        alias_rows = (
            session.execute(
                select(RecordingSpeakerAlias)
                .where(RecordingSpeakerAlias.recording_speaker_id.in_(speaker_ids))
                .where(RecordingSpeakerAlias.active.is_(True))
                .where(RecordingSpeakerAlias.alias_value == value)
                .where(
                    or_(
                        RecordingSpeakerAlias.valid_from_ms.is_(None),
                        RecordingSpeakerAlias.valid_from_ms <= segment_start_ms,
                    )
                )
                .where(
                    or_(
                        RecordingSpeakerAlias.valid_to_ms.is_(None),
                        RecordingSpeakerAlias.valid_to_ms > segment_start_ms,
                    )
                )
                .order_by(
                    func.coalesce(RecordingSpeakerAlias.valid_from_ms, -1).desc(),
                    RecordingSpeakerAlias.id.desc(),
                )
            )
            .scalars()
            .all()
        )
        speakers_by_id = {speaker.id: speaker for speaker in recording_speakers}
        for alias_row in alias_rows:
            alias_speaker = speakers_by_id.get(alias_row.recording_speaker_id)
            if alias_speaker is None:
                alias_speaker = session.get(
                    RecordingSpeaker, alias_row.recording_speaker_id
                )
            if alias_speaker is None or alias_speaker.recording_id != recording_id:
                continue
            resolved = _resolve_active_recording_speaker(session, alias_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved

    for recording_speaker in recording_speakers:
        if recording_speaker.diarization_label == value:
            resolved = _resolve_active_recording_speaker(session, recording_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved

    for recording_speaker in recording_speakers:
        if matches_speaker_name(recording_speaker.local_name, value):
            resolved = _resolve_active_recording_speaker(session, recording_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved
        if matches_speaker_name(recording_speaker.name, value):
            resolved = _resolve_active_recording_speaker(session, recording_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved
        global_speaker = getattr(recording_speaker, "global_speaker", None)
        if global_speaker and matches_speaker_name(global_speaker.name, value):
            resolved = _resolve_active_recording_speaker(session, recording_speaker)
            _apply_source_run_provenance(session, resolved, source_run_id)
            return resolved

    if not speaker_ids:
        return None

    alias_rows = (
        session.execute(
            select(RecordingSpeakerAlias)
            .where(RecordingSpeakerAlias.recording_speaker_id.in_(speaker_ids))
            .where(RecordingSpeakerAlias.active.is_(True))
            .where(RecordingSpeakerAlias.alias_value == value)
            .order_by(RecordingSpeakerAlias.id.desc())
        )
        .scalars()
        .all()
    )
    speakers_by_id = {speaker.id: speaker for speaker in recording_speakers}
    for alias_row in alias_rows:
        alias_speaker = speakers_by_id.get(alias_row.recording_speaker_id)
        if alias_speaker is None:
            alias_speaker = session.get(
                RecordingSpeaker, alias_row.recording_speaker_id
            )
        if alias_speaker is None or alias_speaker.recording_id != recording_id:
            continue
        resolved = _resolve_active_recording_speaker(session, alias_speaker)
        _apply_source_run_provenance(session, resolved, source_run_id)
        return resolved

    return None


def _append_boundary_revision_events(
    session,
    *,
    previous_utterances: Sequence[TranscriptUtterance],
    new_utterances: Sequence[TranscriptUtterance],
    processing_run_id: int | None,
    source: str,
) -> None:
    if not previous_utterances or not new_utterances:
        return

    overlapping_new_by_old: dict[int, list[TranscriptUtterance]] = defaultdict(list)
    overlapping_old_by_new: dict[int, list[TranscriptUtterance]] = defaultdict(list)
    for previous_utterance in previous_utterances:
        for new_utterance in new_utterances:
            if not _utterance_ranges_overlap(previous_utterance, new_utterance):
                continue
            overlapping_new_by_old[previous_utterance.id].append(new_utterance)
            overlapping_old_by_new[new_utterance.id].append(previous_utterance)

    for new_utterance in new_utterances:
        source_utterances = overlapping_old_by_new.get(new_utterance.id, [])
        if len(source_utterances) > 1:
            _append_utterance_event(
                session,
                utterance=new_utterance,
                processing_run_id=processing_run_id,
                event_type="merge",
                source=source,
                old_values={
                    "source_public_ids": [
                        utterance.public_id for utterance in source_utterances
                    ],
                },
                new_values={
                    "start_ms": new_utterance.start_ms,
                    "end_ms": new_utterance.end_ms,
                    "text": new_utterance.text,
                    "speaker": new_utterance.speaker_label,
                },
                resulting_revision=new_utterance.revision,
            )
            continue

        if len(source_utterances) != 1:
            continue

        source_utterance = source_utterances[0]
        if len(overlapping_new_by_old.get(source_utterance.id, [])) <= 1:
            continue

        _append_utterance_event(
            session,
            utterance=new_utterance,
            processing_run_id=processing_run_id,
            event_type="split",
            source=source,
            old_values={"source_public_id": source_utterance.public_id},
            new_values={
                "start_ms": new_utterance.start_ms,
                "end_ms": new_utterance.end_ms,
                "text": new_utterance.text,
                "speaker": new_utterance.speaker_label,
            },
            resulting_revision=new_utterance.revision,
        )


def _append_speaker_correction_event(
    session,
    *,
    recording_id: int,
    actor_user_id: int | None,
    event_type: SpeakerCorrectionEventType,
    scope: SpeakerCorrectionScope,
    utterance_id: int | None = None,
    source_recording_speaker_id: int | None = None,
    target_recording_speaker_id: int | None = None,
    target_global_speaker_id: int | None = None,
    effective_from_ms: int | None = None,
    payload: dict[str, Any] | None = None,
    update_source_provenance: bool = False,
) -> SpeakerCorrectionEvent:
    correction_event = SpeakerCorrectionEvent(
        recording_id=recording_id,
        actor_user_id=actor_user_id,
        utterance_id=utterance_id,
        source_recording_speaker_id=source_recording_speaker_id,
        target_recording_speaker_id=target_recording_speaker_id,
        target_global_speaker_id=target_global_speaker_id,
        event_type=event_type,
        scope=scope,
        effective_from_ms=effective_from_ms,
        payload=payload,
    )
    session.add(correction_event)
    session.flush()

    if target_recording_speaker_id is not None:
        target_speaker = session.get(RecordingSpeaker, target_recording_speaker_id)
        if target_speaker is not None:
            ensure_recording_speaker_aliases_for_speaker(session, target_speaker)
            target_speaker.last_speaker_correction_event_id = correction_event.id
            session.add(target_speaker)

    if (
        update_source_provenance
        and source_recording_speaker_id is not None
        and source_recording_speaker_id != target_recording_speaker_id
    ):
        source_speaker = session.get(RecordingSpeaker, source_recording_speaker_id)
        if source_speaker is not None:
            source_speaker.last_speaker_correction_event_id = correction_event.id
            session.add(source_speaker)

    return correction_event


def record_recording_speaker_corrections(
    session,
    *,
    recording_id: int,
    target_recording_speaker_ids: Sequence[int],
    actor_user_id: int | None,
    event_type: SpeakerCorrectionEventType,
    scope: SpeakerCorrectionScope = SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
    target_global_speaker_id: int | None = None,
    payload: dict[str, Any] | None = None,
    payload_by_speaker_id: dict[int, dict[str, Any]] | None = None,
) -> list[SpeakerCorrectionEvent]:
    from .core import (
        _reconcile_completed_windows_from_effective_point,
        refresh_transcript_projection_from_canonical,
    )

    correction_events: list[SpeakerCorrectionEvent] = []
    for speaker_id in target_recording_speaker_ids:
        recording_speaker = session.get(RecordingSpeaker, speaker_id)
        if recording_speaker is None:
            continue

        event_payload = dict(payload or {})
        if payload_by_speaker_id:
            event_payload.update(payload_by_speaker_id.get(speaker_id, {}))
        event_payload.setdefault(
            "diarization_label", recording_speaker.diarization_label
        )
        event_payload.setdefault("target_public_id", recording_speaker.public_id)
        event_payload.setdefault(
            "speaker_name",
            _recording_speaker_display_name(session, recording_speaker),
        )

        correction_events.append(
            _append_speaker_correction_event(
                session,
                recording_id=recording_id,
                actor_user_id=actor_user_id,
                event_type=event_type,
                scope=scope,
                target_recording_speaker_id=recording_speaker.id,
                target_global_speaker_id=(
                    target_global_speaker_id
                    if target_global_speaker_id is not None
                    else recording_speaker.global_speaker_id
                ),
                effective_from_ms=recording_speaker.first_seen_ms,
                payload=event_payload or None,
            )
        )

    for correction_event in correction_events:
        replay_policy = _speaker_replay_policy_for_correction_event(
            session,
            recording_id=recording_id,
            event_type=correction_event.event_type,
            target_recording_speaker_id=correction_event.target_recording_speaker_id,
        )
        if replay_policy is None:
            continue
        _reconcile_completed_windows_from_effective_point(
            session,
            recording_id=recording_id,
            effective_from_ms=correction_event.effective_from_ms,
            source=_replay_source_for_policy(replay_policy),
            replay_policy=replay_policy,
        )

    return correction_events


def update_recording_speaker_identity(
    session,
    *,
    recording_id: int,
    diarization_label: str,
    new_speaker_name: str,
    target_global_speaker_id: int | None = None,
    actor_user_id: int | None = None,
    merge_global_embedding_alpha: float | None = None,
    event_type: SpeakerCorrectionEventType | None = None,
    source: str = "api",
) -> list[RecordingSpeaker]:
    from .core import (
        list_active_utterances,
        recording_ready_for_canonical_backfill,
        refresh_transcript_projection_from_canonical,
    )
    from .startup import ensure_canonical_backfill

    recording = session.get(Recording, recording_id)
    if recording is None:
        raise LookupError("Recording not found")

    if recording_ready_for_canonical_backfill(recording.status):
        ensure_canonical_backfill(session, recording_id)

    recording_speakers = ensure_recording_speaker_aliases(session, recording_id)
    matching_speakers = [
        recording_speaker
        for recording_speaker in recording_speakers
        if recording_speaker.diarization_label == diarization_label
        and not recording_speaker.merged_into_id
    ]
    if not matching_speakers:
        raise LookupError(f"Speaker '{diarization_label}' not found in recording")

    old_display_names = {
        recording_speaker.id: _recording_speaker_display_name(
            session, recording_speaker
        )
        for recording_speaker in matching_speakers
    }

    target_global_speaker = None
    if target_global_speaker_id is not None:
        target_global_speaker = session.execute(
            select(GlobalSpeaker)
            .where(GlobalSpeaker.id == target_global_speaker_id)
            .where(GlobalSpeaker.user_id == recording.user_id)
        ).scalar_one_or_none()
        if target_global_speaker is None:
            raise LookupError("Global speaker not found")

    for recording_speaker in matching_speakers:
        if target_global_speaker is not None:
            recording_speaker.global_speaker_id = target_global_speaker.id
            recording_speaker.global_speaker = target_global_speaker
            recording_speaker.local_name = None
            if merge_global_embedding_alpha is not None and recording_speaker.embedding:
                if target_global_speaker.embedding:
                    target_global_speaker.embedding = merge_embeddings(
                        target_global_speaker.embedding,
                        recording_speaker.embedding,
                        alpha=merge_global_embedding_alpha,
                    )
                else:
                    target_global_speaker.embedding = list(recording_speaker.embedding)
                session.add(target_global_speaker)
        else:
            recording_speaker.global_speaker_id = None
            recording_speaker.global_speaker = None
            recording_speaker.local_name = new_speaker_name

        recording_speaker.name = None
        recording_speaker.identity_confidence = 1.0
        recording_speaker.identity_locked = True
        ensure_recording_speaker_aliases_for_speaker(session, recording_speaker)
        session.add(recording_speaker)

    effective_event_type = event_type or (
        SpeakerCorrectionEventType.LINK_GLOBAL_SPEAKER
        if target_global_speaker is not None
        else SpeakerCorrectionEventType.RENAME
    )
    record_recording_speaker_corrections(
        session,
        recording_id=recording_id,
        target_recording_speaker_ids=[
            recording_speaker.id for recording_speaker in matching_speakers
        ],
        actor_user_id=actor_user_id,
        event_type=effective_event_type,
        scope=SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
        target_global_speaker_id=(
            target_global_speaker.id if target_global_speaker is not None else None
        ),
        payload_by_speaker_id={
            recording_speaker.id: {
                "old_name": old_display_names.get(recording_speaker.id),
                "new_name": (
                    target_global_speaker.name
                    if target_global_speaker is not None
                    else new_speaker_name
                ),
                "matched_global_speaker": target_global_speaker is not None,
                "source": source,
            }
            for recording_speaker in matching_speakers
        },
    )

    if list_active_utterances(session, recording_id):
        refresh_transcript_projection_from_canonical(session, recording_id)

    return matching_speakers


def merge_recording_speakers_by_label(
    session,
    *,
    recording_id: int,
    source_diarization_label: str,
    target_diarization_label: str,
    actor_user_id: int | None = None,
    source: str = "api",
) -> tuple[RecordingSpeaker, RecordingSpeaker]:
    from .core import (
        _reconcile_completed_windows_from_effective_point,
        list_active_utterances,
        recording_ready_for_canonical_backfill,
        refresh_transcript_projection_from_canonical,
        update_utterance_speaker,
    )
    from .startup import ensure_canonical_backfill

    recording = session.get(Recording, recording_id)
    if recording is None:
        raise LookupError("Recording not found")

    if source_diarization_label == target_diarization_label:
        raise ValueError("Cannot merge speaker into itself")

    if recording_ready_for_canonical_backfill(recording.status):
        ensure_canonical_backfill(session, recording_id)

    recording_speakers = ensure_recording_speaker_aliases(session, recording_id)
    source_matches = [
        recording_speaker
        for recording_speaker in recording_speakers
        if recording_speaker.diarization_label == source_diarization_label
    ]
    target_matches = [
        recording_speaker
        for recording_speaker in recording_speakers
        if recording_speaker.diarization_label == target_diarization_label
    ]

    source_speaker = (
        _resolve_active_recording_speaker(session, source_matches[0])
        if source_matches
        else None
    )
    target_speaker = (
        _resolve_active_recording_speaker(session, target_matches[0])
        if target_matches
        else None
    )

    if source_speaker is None:
        raise LookupError(f"Source speaker '{source_diarization_label}' not found")
    if target_speaker is None:
        raise LookupError(f"Target speaker '{target_diarization_label}' not found")
    if source_speaker.id == target_speaker.id:
        raise ValueError("Cannot merge speaker into itself")

    if source_speaker.embedding:
        if target_speaker.embedding:
            target_speaker.embedding = merge_embeddings(
                target_speaker.embedding,
                source_speaker.embedding,
                alpha=0.5,
            )
        else:
            target_speaker.embedding = list(source_speaker.embedding)
        session.add(target_speaker)

    source_utterances = [
        utterance
        for utterance in list_active_utterances(session, recording_id)
        if (utterance.recording_speaker_id or utterance.speaker_label)
        == source_speaker.id
    ]
    if not source_utterances:
        source_utterances = [
            utterance
            for utterance in list_active_utterances(session, recording_id)
            if (utterance.recording_speaker_id or utterance.speaker_label)
            == source_speaker.diarization_label
        ]

    if source_utterances:
        update_utterance_speaker(
            session,
            recording_id=recording_id,
            utterance_public_id=source_utterances[0].public_id,
            new_speaker_name=_recording_speaker_display_name(session, target_speaker),
            diarization_label=target_speaker.diarization_label,
            scope=SpeakerCorrectionScope.MERGE_INTO_SPEAKER,
            actor_user_id=actor_user_id,
            source=source,
        )
    else:
        source_speaker.merged_into_id = target_speaker.id
        source_speaker.speaker_status = "merged"
        merge_recording_speaker_aliases(
            session,
            source_speaker=source_speaker,
            target_speaker=target_speaker,
        )
        _append_speaker_correction_event(
            session,
            recording_id=recording_id,
            actor_user_id=actor_user_id,
            source_recording_speaker_id=source_speaker.id,
            target_recording_speaker_id=target_speaker.id,
            target_global_speaker_id=target_speaker.global_speaker_id,
            event_type=SpeakerCorrectionEventType.MERGE_SPEAKERS,
            scope=SpeakerCorrectionScope.MERGE_INTO_SPEAKER,
            effective_from_ms=source_speaker.first_seen_ms,
            payload={
                "new_speaker_name": _recording_speaker_display_name(
                    session, target_speaker
                ),
                "diarization_label": target_speaker.diarization_label,
                "target_public_id": target_speaker.public_id,
            },
            update_source_provenance=True,
        )
        replay_policy = _speaker_replay_policy_for_correction_event(
            session,
            recording_id=recording_id,
            event_type=SpeakerCorrectionEventType.MERGE_SPEAKERS,
            target_recording_speaker_id=target_speaker.id,
        )
        _reconcile_completed_windows_from_effective_point(
            session,
            recording_id=recording_id,
            effective_from_ms=source_speaker.first_seen_ms,
            source=_replay_source_for_policy(replay_policy),
            replay_policy=replay_policy,
        )

    source_speaker.embedding = None
    session.add(source_speaker)

    if list_active_utterances(session, recording_id):
        refresh_transcript_projection_from_canonical(session, recording_id)

    return source_speaker, target_speaker


__all__ = [name for name in globals() if not name.startswith("__")]
