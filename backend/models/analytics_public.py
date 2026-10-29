"""Public estimates available before a recording finishes processing."""

from typing import Literal

from pydantic import BaseModel, Field


class AnalyticsSpeakerRead(BaseModel):
    speaker_key: str
    public_id: str | None
    name: str
    diarization_label: str | None
    color: str | None
    global_speaker_id: int | None
    is_named: bool


class AnalyticsTalkTimeRead(BaseModel):
    speech_ms: int
    share_of_speech: float
    share_of_duration: float


class AnalyticsTurnStructureRead(BaseModel):
    turn_count: int
    median_turn_ms: int
    longest_turn_ms: int
    longest_turn_start_ms: int
    excluded_short_turns: int


class TranscriptWpmRead(BaseModel):
    words_per_minute: int | None
    sample_count: int
    excluded_short: int
    excluded_overlapping: int


class LiveAnalyticsMetricsRead(BaseModel):
    utterance_count: int = 0
    talk_time: dict[str, AnalyticsTalkTimeRead] = Field(default_factory=dict)
    turn_structure: dict[str, AnalyticsTurnStructureRead] = Field(default_factory=dict)


class LiveRecordingAnalyticsRead(BaseModel):
    recording_id: str
    provisional: Literal[True] = True
    change_sequence: int = 0
    speakers: list[AnalyticsSpeakerRead] = Field(default_factory=list)
    metrics: LiveAnalyticsMetricsRead = Field(default_factory=LiveAnalyticsMetricsRead)
    transcript_wpm: dict[str, TranscriptWpmRead] = Field(default_factory=dict)
