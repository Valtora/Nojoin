"""Database queries and provisional transcript writes for live transcription."""

import logging
from dataclasses import dataclass

from sqlmodel import select

from backend.models.pipeline import (
    ProcessingRunKind,
    RecordingAudioChunk,
    RecordingAudioWindowManifest,
    TranscriptUtteranceState,
)
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.audio_windows import (
    WINDOW_STATUS_LIVE_PROCESSED,
    mark_audio_windows_processed,
)
from backend.utils.canonical_pipeline import append_utterances_from_segments
from backend.utils.config_manager import config_manager, is_meeting_edge_enabled
from backend.utils.speaker_names.live_suggestions import has_unnamed_live_labels
from backend.utils.transcript_settling import (
    CapturePublicationToken,
    load_capture_publication,
    publication_is_current,
    resolve_capture_settling_policy,
)

logger = logging.getLogger("backend.processing.live_transcribe")


@dataclass(frozen=True)
class LivePublicationResult:
    """Separate rejected work from accepted work that needs no follow-up."""

    accepted: bool
    dispatch_meeting_edge: bool = False
    queue_live_naming: bool = False


def _load_recording_audio_window_manifests(
    session, recording_id: int
) -> list[RecordingAudioWindowManifest]:
    if not hasattr(session, "exec"):
        return []

    try:
        return session.exec(
            select(RecordingAudioWindowManifest)
            .where(RecordingAudioWindowManifest.recording_id == recording_id)
            .order_by(RecordingAudioWindowManifest.window_index)
        ).all()
    except Exception:  # noqa: BLE001 -- boundary: the live lane must keep transcribing when the manifest table is unreadable
        return []


def _load_recording_audio_chunks(
    session, recording_id: int
) -> list[RecordingAudioChunk]:
    if not hasattr(session, "exec"):
        return []

    try:
        return session.exec(
            select(RecordingAudioChunk)
            .where(RecordingAudioChunk.recording_id == recording_id)
            .order_by(RecordingAudioChunk.sequence_no)
        ).all()
    except Exception:  # noqa: BLE001 -- boundary: the live lane must keep transcribing when the chunk table is unreadable
        return []


def _persist_live_run(
    *,
    recording_id: int,
    sequence: int,
    run: list[int],
    new_segments: list[dict],
    state: dict,
    live_config: dict,
    live_config_hash: str,
    live_model_name: str,
    expected_token: CapturePublicationToken | None = None,
) -> LivePublicationResult:
    """Persist provisional live utterances and manifest coverage.

    Re-reads recording status under a fresh session: the live↔final alignment
    invariant requires that provisional writes only land while the recording is
    still in flight (UPLOADING/PAUSED); a late status flip to a terminal/final
    state rejects the result without advancing the obsolete lane checkpoint.
    Returns acceptance, whether a Meeting Edge refresh should be dispatched,
    and whether a live naming check should be queued.
    """
    from backend.core.db import get_sync_session

    should_dispatch_meeting_edge = False
    should_queue_live_naming = False
    session = get_sync_session()
    try:
        transcript, recording = load_capture_publication(session, recording_id)
        if publication_is_current(recording, transcript, expected_token):
            resolve_capture_settling_policy(
                transcript, device=live_config.get("_settling_device")
            )
            use_canonical_live_writes = (
                bool(config_manager.get("enable_canonical_transcript_writes", True))
                and hasattr(session, "exec")
                and hasattr(session, "execute")
            )
            if new_segments:
                _mark_pending_speakers(new_segments, live_config)
                if use_canonical_live_writes:
                    append_utterances_from_segments(
                        session,
                        recording_id=recording_id,
                        segments=new_segments,
                        run_kind=ProcessingRunKind.LIVE,
                        source="live",
                        state_override=TranscriptUtteranceState.PROVISIONAL,
                        trigger_source="worker",
                        config_hash=live_config_hash,
                        transcription_backend=str(
                            live_config.get("transcription_backend") or "whisper"
                        ),
                        model_metadata={
                            "model_name": live_model_name,
                            "chunk_start_sequence": run[0],
                            "chunk_end_sequence": run[-1],
                            "trigger_sequence": sequence,
                        },
                    )
                else:
                    from sqlalchemy.orm.attributes import flag_modified

                    transcript = recording.transcript
                    if transcript is not None:
                        transcript.segments = (transcript.segments or []) + new_segments
                        flag_modified(transcript, "segments")
                        session.add(transcript)

            if new_segments or live_config.get("_settling_public_changed"):
                user_settings = getattr(
                    getattr(recording, "user", None),
                    "settings",
                    None,
                )
                if user_settings is None and getattr(recording, "user_id", None):
                    from backend.models.user import User

                    user = session.get(User, recording.user_id)
                    user_settings = getattr(user, "settings", None) if user else None

                should_dispatch_meeting_edge = is_meeting_edge_enabled(user_settings)
                # Live labels exist only on canonical utterances.
                should_queue_live_naming = (
                    use_canonical_live_writes
                    and transcript is not None
                    and has_unnamed_live_labels(session, transcript)
                )

            manifest_rows = _load_recording_audio_window_manifests(
                session, recording_id
            )
            updated_manifest_rows = mark_audio_windows_processed(
                manifest_rows,
                up_to_sequence=run[-1],
                status=WINDOW_STATUS_LIVE_PROCESSED,
            )
            for manifest_row in updated_manifest_rows:
                session.add(manifest_row)

            session.commit()

            if new_segments:
                record_pipeline_metric(
                    stage="live_segments_persisted",
                    recording_id=recording_id,
                    payload={
                        "sequence": sequence,
                        "segment_count": len(new_segments),
                        "first_segment_start_s": round(
                            min(segment["start"] for segment in new_segments),
                            3,
                        ),
                        "last_segment_end_s": round(
                            max(segment["end"] for segment in new_segments),
                            3,
                        ),
                        "last_speaker_label": state.get("last_speaker_label"),
                    },
                    log=logger,
                )
            return LivePublicationResult(
                True, should_dispatch_meeting_edge, should_queue_live_naming
            )
    finally:
        session.close()
    return LivePublicationResult(False)


def live_naming_wanted_after_labels(recording_id: int) -> bool:
    """Whether a label write that changed lines leaves a label open to a name.

    A first introduction is usually published before its line has a label, so
    its publication finds nothing to name; the label write that follows is the
    first point a naming check can act on it.
    """
    from backend.core.db import get_sync_session

    session = get_sync_session()
    try:
        transcript, recording = load_capture_publication(
            session, recording_id, lock=False
        )
        return (
            transcript is not None
            and publication_is_current(recording, transcript, None)
            and has_unnamed_live_labels(session, transcript)
        )
    finally:
        session.close()


def _mark_pending_speakers(segments: list[dict], config: dict) -> None:
    """Mark whole-line CUDA text before its independent label step can cover it."""
    from backend.processing.live_diarization.setting import (
        live_speaker_labels_requested,
    )

    device = str(config.get("_settling_device") or "")
    if not device.startswith("cuda") or not live_speaker_labels_requested():
        return
    for segment in segments:
        if segment.get("speaker") != "UNKNOWN" or segment.get(
            "speaker_manually_edited"
        ):
            continue
        payload = dict(segment.get("confidence_payload") or {})
        payload["live_speaker_label"] = {
            "locked": False,
            "share": None,
            "diarisation_pending": True,
        }
        segment["confidence_payload"] = payload
