import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Optional

import numpy as np

from backend.processing.embedding_version import (
    EMBEDDING_METHOD_VERSION,
    LEGACY_EMBEDDING_METHOD_VERSION,
)

logger = logging.getLogger(__name__)

# --- Speaker Identification Thresholds ---
# Minimum cosine similarity to consider an automatic speaker match during processing
IDENTIFICATION_THRESHOLD = 0.75
# Minimum cosine similarity required before auto-updating a global speaker's embedding
AUTO_UPDATE_THRESHOLD = 0.85
# Minimum margin between best and second-best match to avoid ambiguous assignments
MARGIN_OF_VICTORY = 0.05
# Minimum similarity between current and incoming embedding to permit a merge.
# Measured on AMI meetings and a House of Lords sitting, with the rule fixed
# before the run. One wrong fold at alpha 0.5 left unguarded made up to two
# thirds of the stranger's clusters identify as the person. Every wrong fold
# scored below 0.65, while at 0.70 every genuine fold of 20 s or 60 s of the
# person's speech was still accepted at alpha 0.3, the rename and link weight
# (at 0.75, AMI accepted 94% of 20 s folds and all 60 s folds). Two held-out
# AMI meetings confirmed it: the weakest genuine fold scored 0.74.
DRIFT_GUARD_THRESHOLD = 0.70
# UI: minimum similarity to display a potential match to the user
UI_SHOW_MATCH_THRESHOLD = 0.50
# UI: minimum similarity (with margin check) to flag a match as "strong"
UI_STRONG_MATCH_THRESHOLD = 0.75
# Embedding-based speaker deduplication: cosine similarity above which two
# RecordingSpeaker rows within the same recording are merged automatically.
DUPLICATE_SPEAKER_MERGE_THRESHOLD = 0.70
# Duplicate merge pass: minimum cosine between a cluster and an identified
# person's library voiceprint before the cluster may join that person's group.
# It was set equal to DRIFT_GUARD_THRESHOLD when the guard was 0.60, and the two
# are now separate on purpose. The guard decides whether a library voiceprint
# changes, and a wrong fold there misleads every later meeting, so it is the
# stricter of the two. This floor only decides whether a cluster joins a group
# in one recording. On this project's own library no two people's recording
# voiceprints scored above 0.55, while the same person's sat at a median of
# 0.87 with 5% below 0.73.
IDENTIFIED_MERGE_VOICEPRINT_FLOOR = 0.60
# Least speech a person's library voiceprint may be created from: the time
# covered by the crops _select_voiceprint_crops picked for the recording
# voiceprint it is created from. Measured on AMI meetings and a House of Lords
# sitting with a rule fixed before the run: the shortest enrolment at which, and
# above which, at most 5% of same-person whole-recording voiceprints scored
# below IDENTIFIED_MERGE_VOICEPRINT_FLOOR and identification stayed within 90%
# of a full enrolment's. AMI set it (Lords passed from 5 s). At 2-3 s, 23-28% of AMI
# speakers were refused and 27-35% identified; at 20 s, none were refused and
# all were identified. Two held-out AMI meetings confirmed it: 0% refused, 95%
# identified, no stranger admitted.
LIBRARY_VOICEPRINT_MIN_SPEECH_S = 20.0
# Default threshold for the scan-matches endpoint
SCAN_MATCH_THRESHOLD = 0.75


def embedding_version_of(obj) -> int:
    """Read the extraction method version off a speaker row.

    Rows written before versioning existed carry ``NULL`` and are legacy by
    definition, so the absent value maps to version 1 rather than to the
    current version.
    """
    return _method_version(getattr(obj, "embedding_version", None))


def _method_version(value) -> int:
    """A stored ``embedding_version`` value as a method version."""
    if value is None:
        return LEGACY_EMBEDDING_METHOD_VERSION
    try:
        return int(value)
    except (TypeError, ValueError):
        return LEGACY_EMBEDDING_METHOD_VERSION


def embeddings_are_comparable(a, b) -> bool:
    """True when two speaker rows' voiceprints may be scored against each other.

    Cosine similarity between embeddings produced by different extraction
    methods is not meaningful -- the vectors occupy different regions of the
    space -- so a cross-version score must never be used to merge speakers or
    identify a person. Stale voiceprints are repaired by re-extraction, not by
    comparing them anyway.
    """
    return embedding_version_of(a) == embedding_version_of(b)


def cosine_similarity(v1: Optional[List[float]], v2: Optional[List[float]]) -> float:
    """Compute cosine similarity between two vectors."""
    if v1 is None or v2 is None:
        return 0.0

    # Check for None values inside the lists which can cause numpy errors
    if any(x is None for x in v1) or any(x is None for x in v2):
        return 0.0

    try:
        a = np.array(v1, dtype=float)
        b = np.array(v2, dtype=float)
    except (ValueError, TypeError):
        return 0.0

    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def merge_embeddings(
    current_embedding: List[float],
    new_embedding: List[float],
    alpha: float = 0.1,
    drift_guard: bool = True,
) -> List[float]:
    """
    Merges a new embedding into an existing one using a weighted moving average.

    Args:
        current_embedding: The existing embedding vector.
        new_embedding: The new embedding vector to merge.
        alpha: The weight of the new embedding (0.0 to 1.0).
               Higher alpha means the new embedding has more influence.
        drift_guard: If True, reject the merge when the new embedding is too
                     dissimilar to the current one (below DRIFT_GUARD_THRESHOLD).

    Returns:
        The merged embedding vector, or the original if drift guard rejects the merge.
    """
    if not current_embedding:
        return new_embedding

    # Reject merges that would corrupt the embedding with unrelated voice data
    if drift_guard and drift_guard_refuses(current_embedding, new_embedding):
        return current_embedding

    curr_arr = np.array(current_embedding)
    new_arr = np.array(new_embedding)

    merged = (1 - alpha) * curr_arr + alpha * new_arr

    return merged.tolist()


def drift_guard_refuses(
    current_embedding: List[float], new_embedding: List[float]
) -> bool:
    """True when ``new_embedding`` is too unlike ``current_embedding`` to fold in."""
    similarity = cosine_similarity(current_embedding, new_embedding)
    if similarity < DRIFT_GUARD_THRESHOLD:
        # Only the verdict: a fold leaves the voiceprint as it was, and a
        # judgement records a refusal (``judge_drift``).
        logger.warning(
            "Drift guard refused a voiceprint: similarity %.3f is below %.2f",
            similarity,
            DRIFT_GUARD_THRESHOLD,
        )
        return True
    return False


class VoiceprintBlend(str, Enum):
    """What ``blend_voiceprint`` did to a row's voiceprint."""

    # The row had none and took a copy of the incoming one.
    COPIED = "copied"
    # The incoming voiceprint was folded in.
    FOLDED = "folded"
    # The drift guard refused the fold; the row is unchanged.
    REFUSED = "refused"
    # A current-version voiceprint replaced the row's stale one.
    REPLACED = "replaced"
    # A stale voiceprint was offered to a current row, which is unchanged.
    KEPT = "kept"


def blend_voiceprint(
    row: Any,
    embedding: List[float],
    version: Optional[int],
    *,
    alpha: float,
) -> VoiceprintBlend:
    """Write a voiceprint into a speaker row and keep its version true.

    ``row`` is a ``GlobalSpeaker`` or a ``RecordingSpeaker``, and ``version`` is
    the extraction method version ``embedding`` was produced with (``None`` for
    a voiceprint that predates versioning). Every path that blends another
    row's voiceprint into a speaker row goes through here, because
    identification only compares voiceprints of one version: a row whose
    version is wrong is ignored, or compared with vectors it can't be compared
    with. A path that copies a vector outright, such as restoring a backup,
    copies its version alongside it.

    A row without a voiceprint takes a copy of ``embedding`` and its version.
    A row of the same version has ``embedding`` folded in at ``alpha``, unless
    the drift guard refuses it. Two versions are never blended, since the
    average of two unrelated regions of the space matches neither: a
    current-version ``embedding`` replaces a stale row voiceprint without the
    drift guard (a cosine across versions says nothing), and a stale
    ``embedding`` leaves a current row as it is.
    """
    if not row.embedding:
        row.embedding = list(embedding)
        row.embedding_version = version
        return VoiceprintBlend.COPIED
    incoming = _method_version(version)
    if embedding_version_of(row) == incoming:
        if drift_guard_refuses(row.embedding, embedding):
            return VoiceprintBlend.REFUSED
        row.embedding = merge_embeddings(
            row.embedding, embedding, alpha=alpha, drift_guard=False
        )
        return VoiceprintBlend.FOLDED
    if incoming == EMBEDDING_METHOD_VERSION:
        row.embedding = list(embedding)
        row.embedding_version = version
        return VoiceprintBlend.REPLACED
    return VoiceprintBlend.KEPT


def _blend_would_found(row: Any, version: Optional[int]) -> bool:
    """True when blending a voiceprint of ``version`` would replace, not fold.

    That is the case for a row without a voiceprint and for a stale one that a
    current-version voiceprint replaces outright.
    """
    if not row.embedding:
        return True
    incoming = _method_version(version)
    return (
        incoming != embedding_version_of(row) and incoming == EMBEDDING_METHOD_VERSION
    )


@dataclass(frozen=True)
class RecordingVoiceprint:
    """A recording voiceprint, the speech behind it and its extraction version.

    ``speech_seconds`` is the time covered by the crops the voiceprint was
    built from, or ``None`` when it predates speech being measured.
    """

    embedding: List[float]
    speech_seconds: Optional[float]
    version: Optional[int] = EMBEDDING_METHOD_VERSION

    @classmethod
    def of_speaker(cls, speaker: Any) -> "RecordingVoiceprint":
        """The voiceprint stored on a ``RecordingSpeaker`` row."""
        return cls(
            embedding=list(speaker.embedding),
            speech_seconds=speaker.embedding_speech_seconds,
            version=speaker.embedding_version,
        )

    def to_task_result(self) -> dict:
        """The JSON shape a Celery task returns this in."""
        return {"embedding": self.embedding, "speech_seconds": self.speech_seconds}

    @classmethod
    def from_task_result(cls, value: Any) -> Optional["RecordingVoiceprint"]:
        """Read ``extract_measured_embedding_task``'s result back.

        ``None`` when extraction produced nothing; otherwise the dict
        ``to_task_result`` writes. A result without a speech figure, such as
        the bare vector ``extract_embedding_task`` returns, is refused rather
        than read as unmeasured: a voiceprint with no figure is grandfathered
        past the evidence floor, and only voiceprints extracted before the
        figure existed may be.
        """
        if not value:
            return None
        seconds = value.get("speech_seconds") if isinstance(value, dict) else None
        if seconds is None:
            raise TypeError(
                f"Voiceprint extraction returned a {type(value).__name__} with no "
                "speech figure, not a measured voiceprint"
            )
        return cls(
            embedding=[float(x) for x in value["embedding"]],
            speech_seconds=float(seconds),
        )


class VoiceprintStatus(str, Enum):
    """What happened to a person's library voiceprint when a recording was linked."""

    # The person had no voiceprint, or a stale one; the recording's became theirs.
    SAVED = "saved"
    # A split only: what remained of the original person in the split
    # recordings was too little, so their voiceprint was rebuilt from their
    # other recordings instead.
    SAVED_FROM_OTHER_RECORDINGS = "saved_from_other_recordings"
    # The recording's voiceprint was blended into the person's.
    FOLDED = "folded"
    # The person has no voiceprint and the recording's was built from less than
    # LIBRARY_VOICEPRINT_MIN_SPEECH_S of speech.
    SKIPPED_INSUFFICIENT_SPEECH = "skipped_insufficient_speech"
    # The drift guard refused to blend the recording's voiceprint in.
    SKIPPED_DRIFT = "skipped_drift"
    # The person's voiceprint is locked against this update.
    SKIPPED_LOCKED = "skipped_locked"
    # The recording's voiceprint is from an older extraction method than the
    # person's, so it can't be blended in until it is re-extracted.
    SKIPPED_OUTDATED = "skipped_outdated"


@dataclass(frozen=True)
class LibraryVoiceprintUpdate:
    """The outcome of offering one recording voiceprint to a person's library."""

    status: VoiceprintStatus
    speech_seconds: Optional[float]

    @property
    def changed(self) -> bool:
        return self.status in (
            VoiceprintStatus.SAVED,
            VoiceprintStatus.SAVED_FROM_OTHER_RECORDINGS,
            VoiceprintStatus.FOLDED,
        )


_LIBRARY_STATUS_BY_BLEND = {
    VoiceprintBlend.COPIED: VoiceprintStatus.SAVED,
    VoiceprintBlend.REPLACED: VoiceprintStatus.SAVED,
    VoiceprintBlend.FOLDED: VoiceprintStatus.FOLDED,
    VoiceprintBlend.REFUSED: VoiceprintStatus.SKIPPED_DRIFT,
    VoiceprintBlend.KEPT: VoiceprintStatus.SKIPPED_OUTDATED,
}


def meets_library_evidence_floor(speech_seconds: Optional[float]) -> bool:
    """True when a recording voiceprint may found a library voiceprint.

    That takes ``LIBRARY_VOICEPRINT_MIN_SPEECH_S`` of speech. A voiceprint with
    no figure predates measurement and is grandfathered: it founds one as it
    did before the floor existed. It is measured only when something extracts
    it again anyway (reprocessing, the voiceprint dialog's extract, a split's
    remainder, or a rebuild after an extraction-method change), never by a
    sweep of its own. Its speech can't be worked out from what is stored,
    because the crops it was built from depended on diarisation turns and
    overlap that are not kept. Every new extraction records a figure.
    """
    if speech_seconds is None:
        return True
    return speech_seconds >= LIBRARY_VOICEPRINT_MIN_SPEECH_S


@dataclass(frozen=True)
class VoiceprintPool:
    """One voiceprint per recording, pooled for a recalibration or a split."""

    # The mean of the recordings that meet the floor; None when none does.
    voiceprint: Optional[RecordingVoiceprint]
    # The most measured speech any one offered recording held.
    best_speech_seconds: Optional[float]
    # How many recordings contributed, of how many were offered.
    used: int
    offered: int


def pool_recording_voiceprints(
    voiceprints: List[RecordingVoiceprint],
) -> VoiceprintPool:
    """Pool the voiceprints a recalibration or a split extracted, one per recording.

    Only recordings whose own voiceprint meets the evidence floor contribute.
    The floor was measured on a voiceprint built from that much speech in one
    recording; short extractions from several recordings add up to seconds
    but not to such a voiceprint. The pool's figure is the largest of the
    contributing recordings' figures.
    """
    qualifying = [
        v for v in voiceprints if meets_library_evidence_floor(v.speech_seconds)
    ]
    measured = [v.speech_seconds for v in voiceprints if v.speech_seconds is not None]
    best = max(measured, default=None)
    if not qualifying:
        return VoiceprintPool(None, best, 0, len(voiceprints))
    mean = np.mean(np.array([v.embedding for v in qualifying]), axis=0).tolist()
    pooled_seconds = max(
        (v.speech_seconds for v in qualifying if v.speech_seconds is not None),
        default=None,
    )
    return VoiceprintPool(
        RecordingVoiceprint(mean, pooled_seconds),
        best,
        len(qualifying),
        len(voiceprints),
    )


def too_little_speech_detail(pool: VoiceprintPool) -> str:
    """Why a pool of selected speech could not found a voiceprint."""
    most = (
        f" (the most was {pool.best_speech_seconds:.1f} s)"
        if pool.best_speech_seconds is not None
        else ""
    )
    return (
        f"No selected recording holds {LIBRARY_VOICEPRINT_MIN_SPEECH_S:.0f} s of "
        f"clear speech on its own{most}. Select more of this person's speech "
        "from one recording."
    )


def update_library_voiceprint(
    person: Any,
    voiceprint: RecordingVoiceprint,
    *,
    alpha: float,
    locked: bool = False,
) -> LibraryVoiceprintUpdate:
    """Offer a recording voiceprint to a person's library voiceprint.

    Every path that writes a person's voiceprint from a recording goes through
    here, and ``person`` is changed in place. A person who has a voiceprint of
    the same version gets the recording's folded in at ``alpha``, unless the
    drift guard refuses it. A person who has none, or only a stale one, gets
    the recording's only when it was built from at least
    ``LIBRARY_VOICEPRINT_MIN_SPEECH_S`` of speech; otherwise they keep what
    they had until a later recording that meets the floor is linked to them.
    """
    speech_seconds = voiceprint.speech_seconds
    if locked:
        return LibraryVoiceprintUpdate(VoiceprintStatus.SKIPPED_LOCKED, speech_seconds)
    if _blend_would_found(person, voiceprint.version) and not (
        meets_library_evidence_floor(speech_seconds)
    ):
        return LibraryVoiceprintUpdate(
            VoiceprintStatus.SKIPPED_INSUFFICIENT_SPEECH, speech_seconds
        )
    blend = blend_voiceprint(
        person, voiceprint.embedding, voiceprint.version, alpha=alpha
    )
    return LibraryVoiceprintUpdate(_LIBRARY_STATUS_BY_BLEND[blend], speech_seconds)


def is_drift_refused(speaker: Any) -> bool:
    """True when the drift guard refused this link's voiceprint for its person.

    Such a link stays out of that person's library voiceprint wherever it is
    rebuilt or pooled from links (the voiceprint rebuild, a split), which never
    run the guard again: after an extraction-method change the person's
    voiceprint is of the old version, so a cosine against it means nothing.
    Each refusal names its person (``speaker.drift_refusals``), so one has no
    effect while the speaker is linked to someone else, and applies again if
    the speaker comes back.
    """
    person_id = speaker.global_speaker_id
    return person_id is not None and any(
        refusal.global_speaker_id == person_id for refusal in speaker.drift_refusals
    )


def refuse_drift(speaker: Any, person_id: int) -> None:
    """Record that the drift guard refused this link for ``person_id``.

    Refusals for other people are kept; a second one for the same person is
    not added.
    """
    if any(r.global_speaker_id == person_id for r in speaker.drift_refusals):
        return
    from backend.models.speaker import RecordingSpeakerDriftRefusal

    speaker.drift_refusals.append(
        RecordingSpeakerDriftRefusal(global_speaker_id=person_id)
    )


def lift_drift_refusal(speaker: Any, person_id: int) -> None:
    """Drop this link's refusal for ``person_id``, keeping any for others."""
    for refusal in [
        r for r in speaker.drift_refusals if r.global_speaker_id == person_id
    ]:
        speaker.drift_refusals.remove(refusal)


def forget_drift_verdict(speaker: Any) -> None:
    """Drop all of a link's drift refusals: a split decides about its remainder.

    A split gives the original's speaker the voiceprint of the clips that
    remain, and the moved clips may be what the guard refused. The split
    itself decides whether that remainder joins the original person's
    voiceprint (``apply_split_to_original``), so the refusals for every person
    go with the old voiceprint. Every other new voiceprint for a link is
    judged instead (``judge_drift``), and re-extracting the same speech under
    a new extraction method (the voiceprint rebuild) keeps them: carrying the
    verdict across that change is what they are for.
    """
    speaker.drift_refusals.clear()


def judge_drift(speaker: Any, person: Any) -> None:
    """Judge a link's new voiceprint against the person it is linked to.

    A segment moved onto the link, a speaker merged into it, the voiceprint
    dialog's extract and the extract-all endpoint, and a processing run that
    finds the speaker already there each give the link a voiceprint that is
    offered to no one, and the voice the guard refused is usually still in it.
    So the guard judges it against ``person``, without changing their
    voiceprint, and the verdict is kept as ``record_drift_verdict`` keeps one:
    a refusal adds ``person`` to the people the link was refused for, and a
    pass lifts only their refusal. With no person, or no voiceprint of theirs
    of the same version to compare with, there is no verdict, and the
    refusals stay as they were. A lock makes no difference, since it guards
    the person's voiceprint and this leaves it alone. A verdict is logged
    with the link's and the person's ids, so a refusal can be traced.
    """
    if person is None or not person.embedding or not speaker.embedding:
        return
    if not embeddings_are_comparable(person, speaker):
        return
    if drift_guard_refuses(person.embedding, speaker.embedding):
        refuse_drift(speaker, person.id)
        verdict = "refused"
    else:
        lift_drift_refusal(speaker, person.id)
        verdict = "passed"
    logger.info(
        "Drift guard verdict on recording speaker %s for person %s: %s",
        speaker.id,
        person.id,
        verdict,
    )


def record_drift_verdict(
    speakers: List[Any], person: Any, update: LibraryVoiceprintUpdate
) -> None:
    """Keep on each link what the drift guard said about its voiceprint.

    A refusal adds ``person`` to the people each link was refused for; a save
    or a fold into ``person`` lifts only the refusal for ``person``. Refusals
    for anyone else stay: a save into Eli says nothing about Dana's
    voiceprint, and a refusal for Eli does not replace one for Dana, so a link
    refused for Dana is still refused when it comes back to her. Any other
    outcome (a lock, too little speech, an outdated voiceprint) is no verdict,
    so the refusals stay as they were.
    """
    if update.status == VoiceprintStatus.SKIPPED_DRIFT:
        for speaker in speakers:
            refuse_drift(speaker, person.id)
    elif update.changed:
        for speaker in speakers:
            lift_drift_refusal(speaker, person.id)


def offer_linked_voiceprint(
    person: Any, speaker: Any, *, alpha: float, locked: bool = False
) -> LibraryVoiceprintUpdate:
    """Offer a recording speaker's voiceprint to the person it is linked to.

    ``update_library_voiceprint`` decides the outcome, and the drift guard's
    verdict is kept on the link (``record_drift_verdict``).
    """
    update = update_library_voiceprint(
        person, RecordingVoiceprint.of_speaker(speaker), alpha=alpha, locked=locked
    )
    record_drift_verdict([speaker], person, update)
    return update


def fold_recording_voiceprint(
    target: Any, voiceprint: RecordingVoiceprint, *, alpha: float, person: Any
) -> Optional[float]:
    """Blend one recording voiceprint into another of the same recording.

    ``target`` is changed in place, its measured speech included. A copy or a
    replacement takes ``speech_seconds``. A fold adds the two speech figures
    when both were measured. When either was not, the result is unmeasured:
    it holds speech from before measurement, which is grandfathered past the
    floor, so it behaves as that voiceprint did before the floor existed. A
    fold the drift guard refuses leaves the target as it was. A target whose
    voiceprint changes is judged again against ``person``, the person it is
    linked to, or None (``judge_drift``).

    Returns the speech added to the target's figure, or None when nothing was
    added to a measured figure. A merge keeps it on the merged row, so that
    undoing the merge can take it out again.
    """
    target_seconds = target.embedding_speech_seconds
    speech_seconds = voiceprint.speech_seconds
    blend = blend_voiceprint(
        target, voiceprint.embedding, voiceprint.version, alpha=alpha
    )
    if blend in (VoiceprintBlend.REFUSED, VoiceprintBlend.KEPT):
        return None
    judge_drift(target, person)
    if blend in (VoiceprintBlend.COPIED, VoiceprintBlend.REPLACED):
        target.embedding_speech_seconds = speech_seconds
        return speech_seconds
    if target_seconds is None or speech_seconds is None:
        target.embedding_speech_seconds = None
        return None
    target.embedding_speech_seconds = target_seconds + speech_seconds
    return speech_seconds


_PLACEHOLDER_NAME_PATTERN = re.compile(
    r"^(SPEAKER_\d+|Speaker \d+|Unknown|New Voice .*)$", re.IGNORECASE
)


def _rank_global_speakers(
    embedding: List[float],
    global_speakers: List,
    method_version: Optional[int],
) -> tuple[List[tuple[Any, float]], int]:
    """The ranking, plus how many stale voiceprints it had to leave out."""
    if method_version is None:
        method_version = EMBEDDING_METHOD_VERSION

    ranked: List[tuple[Any, float]] = []
    skipped_stale = 0
    for gs in global_speakers:
        if not gs.embedding or _PLACEHOLDER_NAME_PATTERN.match(gs.name):
            continue
        if embedding_version_of(gs) != method_version:
            skipped_stale += 1
            continue
        ranked.append((gs, cosine_similarity(embedding, gs.embedding)))

    # A stable sort keeps the first of two equal scores first, as before.
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked, skipped_stale


def rank_global_speakers(
    embedding: List[float],
    global_speakers: List,
    method_version: Optional[int] = None,
) -> List[tuple[Any, float]]:
    """Score ``embedding`` against every comparable saved person, best first.

    People with a placeholder name or no voiceprint are left out, and so are
    voiceprints from another extraction method, because a cross-version cosine
    is not a similarity. ``find_matching_global_speaker`` decides on this
    ranking; the pipeline records it, so a miss can say who came closest.
    """
    ranked, _skipped_stale = _rank_global_speakers(
        embedding, global_speakers, method_version
    )
    return ranked


def find_matching_global_speaker(
    embedding: List[float],
    global_speakers: List,
    threshold: float = IDENTIFICATION_THRESHOLD,
    margin: float = MARGIN_OF_VICTORY,
    method_version: Optional[int] = None,
):
    """
    Find the best matching GlobalSpeaker for a given embedding.

    Args:
        embedding: The embedding vector to match.
        global_speakers: List of GlobalSpeaker objects with embeddings.
        threshold: Minimum similarity score to consider a match.
        margin: The minimum difference required between the best and second best match
                to avoid ambiguous assignments.
        method_version: Extraction method version of ``embedding``. Global
                speakers stored under a different version are skipped, because a
                cross-version cosine score is not a meaningful similarity.
                Defaults to the current extraction version.

    Returns:
        Tuple of (best_matching_speaker, similarity_score).
        Returns (None, 0.0) if no match above threshold or if match is ambiguous.
    """
    ranking, skipped_stale = _rank_global_speakers(
        embedding, global_speakers, method_version
    )
    if skipped_stale:
        logger.info(
            "Skipped %d global speaker(s) whose voiceprint predates extraction "
            "method v%d. Re-extract voiceprints to make them matchable again.",
            skipped_stale,
            method_version or EMBEDDING_METHOD_VERSION,
        )
    if not ranking:
        return None, 0.0

    best_match, best_score = ranking[0]
    # Scores at or below zero never counted as a rival, so neither do they here.
    second_best_score = max(ranking[1][1], 0.0) if len(ranking) > 1 else 0.0

    if best_score >= threshold:
        # Check for ambiguity using the margin of victory
        if (best_score - second_best_score) >= margin:
            return best_match, best_score
        # It's an ambiguous match, better to return nothing than a false positive
        return None, 0.0

    return None, max(best_score, 0.0)
