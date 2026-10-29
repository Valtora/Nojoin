"""Capture the transcript and its names once for an independent output run."""

from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, select

from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.utils.canonical_pipeline import (
    build_transcript_segments_for_read,
    get_canonical_transcript_revision,
)
from backend.utils.meeting_notes import (
    build_recording_speaker_map,
    resolve_recording_speaker_name,
)
from backend.utils.speaker_names.live import with_live_speaker_names


@dataclass(frozen=True)
class OutputSnapshot:
    segments: tuple[dict[str, Any], ...]
    speaker_map: dict[str, str]
    event_watermark: int
    generated_through_ms: int


def _cursor(session: Session, recording_id: int) -> tuple[int, int]:
    sequence = session.exec(
        select(Transcript.change_sequence).where(
            Transcript.recording_id == recording_id
        )
    ).first()
    return int(sequence or 0), get_canonical_transcript_revision(session, recording_id)


def capture_output_snapshot(
    session: Session, recording_id: int, *, transcript: Transcript | None = None
) -> OutputSnapshot:
    """Retry a moving live read; never label old input with a newer revision.

    A separate read-only session avoids cached ORM values and leaves the caller's
    pending output changes alone. Both cursors include changes to text and names.
    The returned values contain no ORM objects that can change during generation.
    """
    for _ in range(3):
        with Session(session.get_bind()) as reader:
            before = _cursor(reader, recording_id)
            segments = build_transcript_segments_for_read(reader, recording_id)
            speakers = reader.exec(
                select(RecordingSpeaker).where(
                    RecordingSpeaker.recording_id == recording_id
                )
            ).all()
            active_speakers = [
                speaker
                for speaker in speakers
                if speaker.merged_into_id is None and speaker.speaker_status != "merged"
            ]
            speaker_map = with_live_speaker_names(
                reader, recording_id, build_recording_speaker_map(active_speakers)
            )
            for speaker in speakers:
                name = resolve_recording_speaker_name(speaker)
                if name:
                    speaker_map[f"rs:{speaker.id}"] = name
            ids_by_label = {
                speaker.diarization_label: speaker.id for speaker in active_speakers
            }
            segments = [
                {**segment, "recording_speaker_id": ids_by_label[segment["speaker"]]}
                if segment.get("recording_speaker_id") is None
                and segment.get("speaker") in ids_by_label
                else dict(segment)
                for segment in segments
            ]
            if _cursor(reader, recording_id) == before:
                return OutputSnapshot(
                    segments=tuple(dict(segment) for segment in segments),
                    speaker_map=speaker_map,
                    event_watermark=before[1],
                    generated_through_ms=max(
                        (
                            int(
                                segment.get("end_ms")
                                or float(segment.get("end") or 0) * 1000
                            )
                            for segment in segments
                        ),
                        default=0,
                    ),
                )
    raise RuntimeError(
        "The live transcript changed while its snapshot was being read. Please retry."
    )
