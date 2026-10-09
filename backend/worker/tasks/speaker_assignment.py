"""Speaker assignment and identification stage of the final pipeline.

Extracted from ``pipeline.py`` to keep that module within its size budget. The
stage resolves diarization labels to ``RecordingSpeaker`` rows, matches them to
saved people by voiceprint, and runs the embedding-based duplicate-merge pass.

Two load-bearing invariants run through the whole stage:

* **Manual-edit authority** -- a speaker carrying a ``local_name`` (or a merge
  target) is treated as identified and is never re-matched against global
  voiceprints.
* **Stable-id alignment** -- when a resolved name was already assigned to an
  earlier label, this label is auto-merged into the first one and the in-memory
  ``final_segments`` (and any ``overlapping_speakers``) are rewritten to the
  canonical target label so the transcript stays coherent.
"""

from dataclasses import dataclass

from .constants import *  # noqa: F403


@dataclass
class _ResolvedIdentity:
    """Outcome of resolving one diarization label to a name and person.

    ``is_identified`` gates the sequential "Speaker N" fallback and, more
    importantly, suppresses voiceprint matching for a speaker the user has
    already named or merged by hand.
    """

    name: str
    global_speaker_id: int | None = None
    is_identified: bool = False


@dataclass
class _LabelWork:
    """Everything resolved about one diarization label, ready to persist."""

    recording: Recording
    label: str
    identity: _ResolvedIdentity
    embedding: list | None
    existing_speaker: RecordingSpeaker | None


def _extract_speaker_voiceprints(ctx, recording: Recording, diarization_result) -> dict:
    """Extract a voiceprint per diarized speaker, if enabled."""
    from backend.processing.embedding_core import extract_embeddings

    merged_config = ctx.merged_config
    if not merged_config.get("enable_auto_voiceprints", True):
        logger.info("Skipping voiceprint extraction (enable_auto_voiceprints=False)")
        return {}
    if not diarization_result:
        return {}

    session = ctx.session
    ctx.task.update_state(
        state="PROCESSING", meta={"progress": 90, "stage": "Voiceprints"}
    )
    recording.processing_step = f"Learning voiceprints...{ctx.device_suffix}"
    recording.processing_progress = 90
    session.add(recording)
    session.commit()
    logger.info("Extracting speaker voiceprints (enable_auto_voiceprints=True)")

    return extract_embeddings(
        ctx.processed_audio_path,
        diarization_result,
        device_str=merged_config.get("processing_device", "cpu"),
        config=merged_config,
    )


def _follow_merge_chain(session, speaker: RecordingSpeaker, label: str):
    """Walk to the surviving speaker of a merge chain, guarding against cycles."""
    current = speaker
    visited = {current.id}
    while current.merged_into_id:
        nxt = session.get(RecordingSpeaker, current.merged_into_id)
        if not nxt:
            logger.warning(
                f"Merge chain broken for speaker {label} at ID {current.merged_into_id}"
            )
            break
        if nxt.id in visited:
            logger.warning(f"Circular merge detected for speaker {label}")
            break
        visited.add(nxt.id)
        current = nxt
    return current


def _identity_from_existing_row(
    session, existing_speaker: RecordingSpeaker | None, label: str
) -> _ResolvedIdentity | None:
    """Recover an identity the user (or a previous run) already established.

    Returns ``None`` when the row carries no such authority, leaving the caller
    free to fall through to voiceprint matching.
    """
    if not existing_speaker:
        return None

    if existing_speaker.merged_into_id:
        logger.info("Speaker %s is merged. Resolving target...", label)
        target = _follow_merge_chain(session, existing_speaker, label)
        resolved_name = target.name or target.local_name or target.diarization_label
        logger.info("Resolved %s (Merged) -> %s", label, resolved_name)
        # Identified either way: a global link is honoured, and a purely local
        # merge still represents a decision that must not be re-litigated.
        return _ResolvedIdentity(
            name=resolved_name,
            global_speaker_id=target.global_speaker_id,
            is_identified=True,
        )

    if existing_speaker.local_name:
        logger.info(
            f"Preserving manual name for {label}: {existing_speaker.local_name}"
        )
        return _ResolvedIdentity(
            name=existing_speaker.local_name,
            global_speaker_id=existing_speaker.global_speaker_id,
            is_identified=True,
        )

    return None


def _matchable_global_speakers(session, user_id) -> list:
    """Load this user's people, excluding placeholder-named or empty voiceprints."""
    import re

    placeholder_pattern = re.compile(
        r"^(SPEAKER_\d+|Speaker \d+|Unknown)$", re.IGNORECASE
    )

    candidates = session.exec(
        select(GlobalSpeaker)
        .where(GlobalSpeaker.embedding != None)  # noqa: E711 -- SQL NULL comparison
        .where(GlobalSpeaker.user_id == user_id)
    ).all()

    return [
        gs
        for gs in candidates
        if not placeholder_pattern.match(gs.name)
        and gs.embedding
        and len(gs.embedding) > 0
        and not any(x is None for x in gs.embedding)
    ]


def _learn_from_confident_match(session, best_match, embedding, best_score) -> None:
    """Fold a high-confidence observation back into the person's voiceprint."""
    from backend.processing.embedding import (
        AUTO_UPDATE_THRESHOLD,
        embedding_version_of,
        merge_embeddings,
    )

    if best_match.is_voiceprint_locked:
        return

    if best_score < AUTO_UPDATE_THRESHOLD:
        # Below this, a match is plausible but not trustworthy enough to alter
        # the stored voiceprint with.
        logger.info(
            f"Skipping auto-update for {best_match.name} "
            f"(score {best_score:.2f} < auto-update threshold {AUTO_UPDATE_THRESHOLD})"
        )
        return

    try:
        best_match.embedding = merge_embeddings(best_match.embedding, embedding)
        # The blended vector is only valid under the version both sides were
        # extracted with; matching already guarantees they agree.
        best_match.embedding_version = embedding_version_of(best_match)
        session.add(best_match)
    except Exception as e:  # noqa: BLE001 -- boundary: embedding update is best-effort
        logger.warning(f"Failed to update embedding for {best_match.name}: {e}")


def _identity_from_voiceprint(
    session, recording: Recording, embedding, label: str
) -> _ResolvedIdentity | None:
    """Match a voiceprint against the user's saved people."""
    from backend.processing.embedding import find_matching_global_speaker

    global_speakers = _matchable_global_speakers(session, recording.user_id)

    best_match, best_score = find_matching_global_speaker(
        embedding, global_speakers, threshold=0.75, margin=0.05
    )

    if not best_match:
        logger.info(f"No match found for {label} (Best score: {best_score:.2f}).")
        return None

    logger.info(f"Identified {label} as {best_match.name} (Score: {best_score:.2f})")
    _learn_from_confident_match(session, best_match, embedding, best_score)
    return _ResolvedIdentity(
        name=best_match.name,
        global_speaker_id=best_match.id,
        is_identified=True,
    )


def _rewrite_segment_labels(
    final_segments: list[dict], from_label: str, to_label: str
) -> None:
    """Point every mention of one label at another, primary and overlapping."""
    for seg in final_segments:
        if seg["speaker"] == from_label:
            seg["speaker"] = to_label
        if "overlapping_speakers" in seg:
            for idx, ov_spk in enumerate(seg["overlapping_speakers"]):
                if ov_spk == from_label:
                    seg["overlapping_speakers"][idx] = to_label


def _absorb_into_existing_name(
    session,
    work: _LabelWork,
    *,
    target_id: int,
    target_label: str,
    final_segments: list[dict],
) -> None:
    """Merge this label into the earlier label that already holds its name."""
    from backend.processing.embedding_core import EMBEDDING_METHOD_VERSION

    logger.info(
        f"Auto-Merge: '{work.identity.name}' already assigned to {target_label}. "
        f"Merging {work.label} into {target_label}."
    )

    if work.existing_speaker:
        work.existing_speaker.merged_into_id = target_id
        work.existing_speaker.name = work.identity.name  # Keep consistent name
        work.existing_speaker.local_name = None
        session.add(work.existing_speaker)
    else:
        session.add(
            RecordingSpeaker(
                recording_id=work.recording.id,
                diarization_label=work.label,
                name=work.identity.name,
                embedding=work.embedding,
                embedding_version=(
                    EMBEDDING_METHOD_VERSION if work.embedding is not None else None
                ),
                global_speaker_id=work.identity.global_speaker_id,
                merged_into_id=target_id,
            )
        )
    session.flush()

    # Keep the transcript coherent with the merge that just happened.
    _rewrite_segment_labels(final_segments, work.label, target_label)


def _persist_speaker(session, work: _LabelWork) -> int | None:
    """Create or update the RecordingSpeaker row for one label."""
    from backend.processing.embedding_core import EMBEDDING_METHOD_VERSION

    if not work.existing_speaker:
        rec_speaker = RecordingSpeaker(
            recording_id=work.recording.id,
            diarization_label=work.label,
            name=work.identity.name,
            embedding=work.embedding,
            embedding_version=(
                EMBEDDING_METHOD_VERSION if work.embedding is not None else None
            ),
            global_speaker_id=work.identity.global_speaker_id,
        )
        session.add(rec_speaker)
        session.flush()
        return rec_speaker.id

    if work.embedding is not None:
        work.existing_speaker.embedding = work.embedding
        work.existing_speaker.embedding_version = EMBEDDING_METHOD_VERSION
    elif work.existing_speaker.embedding:
        logger.info(
            "Preserving existing voiceprint for %s because final diarization "
            "produced no embedding.",
            work.label,
        )
    work.existing_speaker.name = work.identity.name
    if (
        work.identity.global_speaker_id is not None
        or work.existing_speaker.global_speaker_id is None
    ):
        work.existing_speaker.global_speaker_id = work.identity.global_speaker_id
    session.add(work.existing_speaker)
    session.flush()
    return work.existing_speaker.id


def _run_duplicate_merge_pass(
    ctx, recording: Recording, final_segments: list[dict], diarization_result
) -> None:
    """Collapse over-clustered speakers by voiceprint similarity.

    Catches duplicates the name-based auto-merge cannot see: two clusters both
    named "Speaker N" before identification, or one person split across two
    rows. Best-effort -- a failure here must not lose an otherwise good
    transcript, but it is reported to the metric stream rather than swallowed.
    """
    from backend.processing.diarization_stats import speech_seconds_by_label

    try:
        from backend.processing.speaker_merge import merge_duplicate_speakers

        merge_pairs = merge_duplicate_speakers(
            ctx.session,
            recording_id=recording.id,
            segments=final_segments,
            # Utterance rows are not written until after this stage on an
            # imported recording, so speech duration is the only survivor
            # evidence available on that path.
            speech_seconds=speech_seconds_by_label(diarization_result),
        )
        if merge_pairs:
            logger.info(
                "[SpeakerMerge] Merged %d duplicate speaker(s) in recording %d",
                len(merge_pairs),
                ctx.recording_id,
            )
    except Exception as e:  # noqa: BLE001 -- boundary: merge pass is best-effort
        logger.warning("[SpeakerMerge] Merge pass failed, continuing: %s", e)
        # A swallowed failure must still be visible in the metric stream,
        # otherwise it is indistinguishable from a pass that merged nothing.
        record_pipeline_metric(
            stage="speaker_merge_pass",
            recording_id=ctx.recording_id,
            payload={"reason": "merge_pass_failed", "error": str(e)},
            status="error",
            log=logger,
        )


def _resolve_identity(
    session,
    *,
    recording: Recording,
    label: str,
    embedding,
    existing_speaker: RecordingSpeaker | None,
) -> _ResolvedIdentity:
    """Resolve one label, preferring established identity over inference."""
    identity = _identity_from_existing_row(session, existing_speaker, label)
    if identity:
        return identity

    if embedding:
        identity = _identity_from_voiceprint(session, recording, embedding, label)
        if identity:
            return identity

    return _ResolvedIdentity(name=label)


def assign_and_identify_speakers(
    ctx,
    recording: Recording,
    final_segments: list[dict],
    diarization_result,
) -> None:
    """Resolve diarization labels to speakers, persisting RecordingSpeaker rows."""
    # Imported here rather than at module scope: pipeline.py imports this module
    # at load time, so a module-level import back into it would cycle.
    from backend.worker.tasks.pipeline import _collect_ordered_final_speaker_labels

    session = ctx.session

    # Processed in order of appearance so the sequential fallback names read as
    # "Speaker 1", "Speaker 2", ... down the transcript.
    ordered_speakers = _collect_ordered_final_speaker_labels(final_segments)
    logger.info(
        f"Extracted {len(ordered_speakers)} unique speakers from segments: {ordered_speakers}"
    )

    speaker_embeddings = _extract_speaker_voiceprints(
        ctx, recording, diarization_result
    )

    speaker_counter = 1
    # name -> {'id': recording_speaker_id, 'label': diarization_label}
    resolved_names_map: dict = {}

    for label in ordered_speakers:
        existing_speaker = session.exec(
            select(RecordingSpeaker)
            .where(RecordingSpeaker.recording_id == recording.id)
            .where(RecordingSpeaker.diarization_label == label)
        ).first()

        embedding = speaker_embeddings.get(label)
        identity = _resolve_identity(
            session,
            recording=recording,
            label=label,
            embedding=embedding,
            existing_speaker=existing_speaker,
        )

        if not identity.is_identified:
            identity.name = f"Speaker {speaker_counter}"
            speaker_counter += 1

        work = _LabelWork(
            recording=recording,
            label=label,
            identity=identity,
            embedding=embedding,
            existing_speaker=existing_speaker,
        )

        target = resolved_names_map.get(identity.name)
        if target and target["label"] != label:
            _absorb_into_existing_name(
                session,
                work,
                target_id=target["id"],
                target_label=target["label"],
                final_segments=final_segments,
            )
            continue

        logger.info("Mapped %s -> %s", label, identity.name)
        speaker_id = _persist_speaker(session, work)
        if identity.name and speaker_id:
            resolved_names_map[identity.name] = {"id": speaker_id, "label": label}

    _run_duplicate_merge_pass(ctx, recording, final_segments, diarization_result)


__all__ = ["assign_and_identify_speakers"]
