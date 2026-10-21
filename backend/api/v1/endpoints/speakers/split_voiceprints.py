"""What splitting a person does to the two people's library voiceprints.

A split moves selected clips from one person (the original) to another (new,
or an existing person of that name), extracting one voiceprint per recording
from the clips moved and one from the clips that remain. Both sides go
through ``pool_recording_voiceprints``, so only recordings that meet the
evidence floor on their own are used, and a link the drift guard refused for
its person is left out (``is_drift_refused``).
"""

from typing import Any, List

from fastapi import HTTPException

from backend.processing.embedding import (
    LibraryVoiceprintUpdate,
    RecordingVoiceprint,
    VoiceprintStatus,
    embedding_version_of,
    forget_drift_verdict,
    is_drift_refused,
    pool_recording_voiceprints,
    record_drift_verdict,
    update_library_voiceprint,
)
from backend.processing.embedding_version import EMBEDDING_METHOD_VERSION

# The weight the moved clips fold into an existing person's voiceprint with.
SPLIT_FOLD_ALPHA = 0.1


def apply_split_to_new_person(
    person: Any, voiceprints: List[RecordingVoiceprint], links: List[Any]
) -> LibraryVoiceprintUpdate:
    """Found or fold the person the clips moved to from the clips moved.

    A split is the user's own correction, so a lock does not block it, as it
    never did here; the drift guard still does, and its verdict is kept on
    ``links``, the recording speakers the clips moved to.
    """
    pool = pool_recording_voiceprints(voiceprints)
    if pool.voiceprint is None:
        return LibraryVoiceprintUpdate(
            VoiceprintStatus.SKIPPED_INSUFFICIENT_SPEECH, pool.best_speech_seconds
        )
    update = update_library_voiceprint(person, pool.voiceprint, alpha=SPLIT_FOLD_ALPHA)
    record_drift_verdict(links, person, update)
    if update.changed:
        person.is_voiceprint_locked = True
    return update


def apply_split_to_original_speaker(
    speaker: Any, voiceprint: RecordingVoiceprint | None
) -> None:
    """Give the original's recording speaker the voiceprint of what remains.

    Its old voiceprint was built from the moved clips too. Left in place, the
    voiceprint rebuild would average it back into the original person. A drift
    refusal of the old voiceprint goes with it: the moved clips may be what
    the guard refused, and what remains has not been judged.
    """
    forget_drift_verdict(speaker)
    if voiceprint is None:
        speaker.embedding = None
        speaker.embedding_version = None
        speaker.embedding_speech_seconds = None
        return
    speaker.embedding = voiceprint.embedding
    speaker.embedding_version = voiceprint.version
    speaker.embedding_speech_seconds = voiceprint.speech_seconds


def other_recording_voiceprints(links: List[Any]) -> List[RecordingVoiceprint]:
    """The voiceprints of the original's links outside the split recordings.

    Only current-version voiceprints can be pooled with each other, and a link
    the drift guard refused for the person stays out, as in the rebuild.
    """
    return [
        RecordingVoiceprint.of_speaker(link)
        for link in links
        if link.embedding
        and embedding_version_of(link) == EMBEDDING_METHOD_VERSION
        and not is_drift_refused(link)
    ]


def apply_split_to_original(
    person: Any,
    remaining: List[RecordingVoiceprint],
    other_recordings: List[RecordingVoiceprint],
) -> LibraryVoiceprintUpdate:
    """Rebuild the original person's voiceprint from what is still theirs.

    The clips that remain in the split recordings come first. When none of
    those recordings holds enough of them, the voiceprint is rebuilt from the
    person's other recordings, pooled the way a recalibration pools its
    samples (``other_recording_voiceprints``). Only when nothing qualifies is
    it cleared, rather than kept: it was built partly from clips the user has
    just said belong to someone else, so it would go on matching that person.
    A cleared voiceprint keeps the current version, because a voiceprint of no
    version reads as legacy and the voiceprint rebuild would found a new one
    from the person's links, unlocked, behind the split's back. The lock stays
    as it was unless the voiceprint is cleared, which unlocks it so that the
    next recording linked with enough speech can found one.
    """
    pool = pool_recording_voiceprints(remaining)
    voiceprint = pool.voiceprint
    status = VoiceprintStatus.SAVED
    if voiceprint is None:
        voiceprint = pool_recording_voiceprints(other_recordings).voiceprint
        status = VoiceprintStatus.SAVED_FROM_OTHER_RECORDINGS
    if voiceprint is None:
        person.embedding = None
        person.embedding_version = EMBEDDING_METHOD_VERSION
        person.is_voiceprint_locked = False
        return LibraryVoiceprintUpdate(
            VoiceprintStatus.SKIPPED_INSUFFICIENT_SPEECH, pool.best_speech_seconds
        )
    person.embedding = voiceprint.embedding
    person.embedding_version = EMBEDDING_METHOD_VERSION
    return LibraryVoiceprintUpdate(status, voiceprint.speech_seconds)


def split_extraction_failed() -> HTTPException:
    """Refuse a split whose voiceprints could not be extracted.

    Nothing is committed until the end of a split, so refusing leaves both
    people as they were, rather than one without a voiceprint and the other
    keeping one that still holds the split-off voice.
    """
    return HTTPException(
        status_code=504,
        detail=(
            "Voiceprint extraction timed out or failed, so nothing was split. "
            "Please try again."
        ),
    )
