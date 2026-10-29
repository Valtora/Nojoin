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
from backend.utils.meeting_brief import get_live_title_delay_minutes
from backend.utils.recording_audio_sync import lock_recording_audio
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
    # The owner's live title delay while the recording still has no gist, else 0.
    live_title_delay_minutes: int = 0


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

    Takes the audio lock and then the Transcript fence, the order Stop, pause and
    resume use; finalize holds both to its commit. Writes only while the capture is
    UPLOADING/PAUSED, so a late status flip rejects the result without advancing
    the obsolete lane checkpoint. Returns acceptance and whether to refresh
    Meeting Edge.
    """
    from backend.core.db import get_sync_session

    should_dispatch_meeting_edge = False
    live_title_delay_minutes = 0
    session = get_sync_session()
    try:
        lock_recording_audio(session, recording_id)
        transcript, recording = load_capture_publication(session, recording_id)
        if publication_is_current(recording, transcript, expected_token):
            resolve_capture_settling_policy(
                transcript, device=live_config.get("_settling_device")
            )
            if new_segments:
                _mark_pending_speakers(new_segments, live_config)
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
                if recording.gist is None:
                    live_title_delay_minutes = get_live_title_delay_minutes(
                        user_settings
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
                True, should_dispatch_meeting_edge, live_title_delay_minutes
            )
    finally:
        session.close()
    return LivePublicationResult(False)


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
