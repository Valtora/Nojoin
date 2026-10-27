"""Bounded, episode-local bookkeeping for delayed diariser speech arrivals."""

from dataclasses import dataclass, field
from typing import Any

from backend.processing.live_settling_audio import chunk_has_speech
from backend.utils.transcript_settling import GateChunk


@dataclass(frozen=True)
class GateObservation:
    """Current capture bounds and evidence, read without waiting for inference."""

    sequence: int
    chunk_start_ms: int
    audio_end_ms: int
    floor_ms: int
    audio: Any
    audio_start_s: float
    vad_spans: list[tuple[float, float]]
    diariser_spans: list[tuple[float, float]]
    drained_chunks: list[tuple[int, int, int]] = field(default_factory=list)


def plan_gate(
    chunks: list[GateChunk], observation: GateObservation
) -> tuple[bool, list[GateChunk], int | None]:
    """Keep the current gate and spend each missed chunk's delayed arrival once."""
    retained = [
        chunk.copy() for chunk in chunks if chunk["end_ms"] > observation.floor_ms
    ]
    current_gate = False
    delayed_starts: list[int] = []
    for chunk in retained:
        if (
            chunk["sequence"] >= observation.sequence
            or not chunk["missed"]
            or chunk["consumed"]
        ):
            continue
        start_ms = max(chunk["start_ms"], observation.floor_ms)
        if chunk_has_speech(
            audio=observation.audio,
            audio_start_s=observation.audio_start_s,
            chunk_bounds_s=(start_ms / 1000, chunk["end_ms"] / 1000),
            vad_spans=[],
            diariser_spans=observation.diariser_spans,
        ):
            chunk["consumed"] = True
            delayed_starts.append(start_ms)
    drained = observation.drained_chunks or [
        (observation.sequence, observation.chunk_start_ms, observation.audio_end_ms)
    ]
    for sequence, start_ms, end_ms in drained:
        if end_ms <= observation.floor_ms or any(
            chunk["sequence"] == sequence for chunk in retained
        ):
            continue
        start_ms = max(start_ms, observation.floor_ms)
        vad = _has_speech(observation, start_ms, end_ms, observation.vad_spans)
        gate = vad or _has_speech(
            observation, start_ms, end_ms, observation.diariser_spans
        )
        current_gate = current_gate or gate
        if gate and sequence < observation.sequence:
            delayed_starts.append(start_ms)
        retained.append(
            GateChunk(
                sequence=sequence,
                start_ms=start_ms,
                end_ms=end_ms,
                missed=not vad,
                consumed=gate,
            )
        )
    # The configured maximum is 30 seconds of two-second chunks. The extra
    # bound also prevents empty or malformed capture chunks accumulating.
    return (
        current_gate or bool(delayed_starts),
        retained[-16:],
        min(delayed_starts, default=None),
    )


def _has_speech(
    observation: GateObservation,
    start_ms: int,
    end_ms: int,
    spans: list[tuple[float, float]],
) -> bool:
    return chunk_has_speech(
        audio=observation.audio,
        audio_start_s=observation.audio_start_s,
        chunk_bounds_s=(start_ms / 1000, end_ms / 1000),
        vad_spans=spans,
        diariser_spans=[],
    )


def decoded_gate_chunks(
    chunks: list[GateChunk], input_start_ms: int, floor_ms: int
) -> list[GateChunk]:
    """A successful window also spends missed chunks it has already decoded."""
    return [
        {**chunk, "consumed": True}
        if max(chunk["start_ms"], floor_ms) >= input_start_ms
        else chunk
        for chunk in chunks
    ]
