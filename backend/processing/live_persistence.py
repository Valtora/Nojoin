"""Database queries and provisional transcript writes for live transcription."""

import logging

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

logger = logging.getLogger("backend.processing.live_transcribe")


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
) -> bool:
    """Persist provisional live utterances and manifest coverage.

    Re-reads recording status under a fresh session: the live↔final alignment
    invariant requires that provisional writes only land while the recording is
    still in flight (UPLOADING/PAUSED); a late status flip to a terminal/final
    state must skip the DB write but otherwise leave the run to advance the lane.
    Returns whether a Meeting Edge refresh should be dispatched by the caller.
    """
    from backend.core.db import get_sync_session
    from backend.models.recording import Recording, RecordingStatus

    should_dispatch_meeting_edge = False
    session = get_sync_session()
    try:
        recording = session.get(Recording, recording_id)
        if recording and recording.status in {
            RecordingStatus.UPLOADING,
            RecordingStatus.PAUSED,
        }:
            if new_segments:
                use_canonical_live_writes = (
                    bool(config_manager.get("enable_canonical_transcript_writes", True))
                    and hasattr(session, "exec")
                    and hasattr(session, "execute")
                )

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
    finally:
        session.close()

    return should_dispatch_meeting_edge
