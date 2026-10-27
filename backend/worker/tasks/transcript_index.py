"""Build transcript search chunks without retaining a fence during embedding."""

import logging
from typing import Any

from sqlmodel import Session

from backend.utils.live_speaker_labels import speaker_display_name
from backend.utils.speaker_names.live import with_live_speaker_names

from .constants import *
from .intelligence_source import (
    intelligence_source_is_current,
    load_intelligence_source,
)

logger = logging.getLogger(__name__)


def _transcript_chunks(
    segments: list[dict[str, Any]], speaker_map: dict[str, str]
) -> tuple[list[str], list[dict[str, Any]]]:
    chunks: list[str] = []
    metadata: list[dict[str, Any]] = []
    content = ""
    start = 0.0
    end = 0.0
    speakers: set[str] = set()
    length = 0
    for segment in segments:
        name = speaker_display_name(segment["speaker"], speaker_map)
        if length == 0:
            start = segment["start"]
        end = segment["end"]
        content += f"{name}: {segment['text']}\n"
        length += len(segment["text"])
        speakers.add(name)
        if length >= 1000:
            chunks.append(content)
            metadata.append(
                {
                    "start": start,
                    "end": end,
                    "speakers": list(speakers),
                    "source": "transcript",
                }
            )
            content, length, speakers = "", 0, set()
    if content:
        chunks.append(content)
        metadata.append(
            {
                "start": start,
                "end": end,
                "speakers": list(speakers),
                "source": "transcript",
            }
        )
    return chunks, metadata


def index_transcript_source(session: Session, recording_id: int) -> None:
    recording = session.get(Recording, recording_id)
    if recording is None:
        return
    transcript = load_intelligence_source(session, recording_id)
    segments = build_transcript_segments_for_read(
        session, recording_id, transcript=transcript
    )
    if transcript is None or not segments:
        return
    speakers = session.exec(
        select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)
    ).all()
    speaker_map = with_live_speaker_names(
        session, recording_id, build_recording_speaker_map(speakers)
    )
    try:
        chunks, metadata = _transcript_chunks(segments, speaker_map)
        session.flush()
        source_revision = transcript.change_sequence
        session.commit()
        if not chunks:
            return
        from backend.processing.text_embedding import get_text_embedding_service

        vectors = get_text_embedding_service().embed(chunks)
        if not intelligence_source_is_current(session, recording_id, source_revision):
            return
        existing = session.exec(
            select(ContextChunk).where(
                ContextChunk.recording_id == recording_id,
                ContextChunk.document_id == None,
            )
        ).all()
        for chunk in existing:
            if chunk.meta and chunk.meta.get("source") == "transcript":
                session.delete(chunk)
        for content, meta, vector in zip(chunks, metadata, vectors):
            session.add(
                ContextChunk(
                    recording_id=recording_id,
                    content=content,
                    embedding=vector,
                    meta=meta,
                )
            )
        session.commit()
        logger.info(
            "Indexed transcript for recording %s: %s chunks.", recording_id, len(chunks)
        )
    except Exception as exc:
        logger.error(
            "Failed to index transcript %s: %s", recording_id, exc, exc_info=True
        )
