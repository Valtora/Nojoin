"""Run one latest-chunk rolling decode outside the transcript publication fence."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from backend.processing.engines.errors import TranscriptionError
from backend.processing.live_asr_payload import (
    _build_live_confidence_payload,
    _prepare_live_region,
)
from backend.processing.live_audio_buffer import (
    _analyze_live_source_channels,
    _build_live_source_channel_evidence,
)
from backend.processing.live_diarization.step import live_speaker_labels_device
from backend.processing.live_limits import DEFAULT_MAX_SEGMENT_S
from backend.processing.live_settling_audio import (
    chunk_has_speech,
    rolling_audio_tail,
    rolling_channel_tail,
)
from backend.processing.processing_tuning import LINE_BREAK_PAUSE_KEY, resolve_tuning
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.live_word_settling import plan_settling_window
from backend.utils.transcript_settling import (
    CapturePublicationToken,
    cpu_settling_allowed,
    current_settling_policy,
    load_capture_publication,
    publication_is_current,
    resolve_capture_settling_policy,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SettlingStep:
    """Capture-clock inputs shared with the completed-region lane."""

    recording_id: int
    sequence: int
    temp_dir: Path
    live_dir: Path
    audio_end_ms: int
    combined_start_s: float
    vad_regions: list[dict[str, Any]]
    expected_token: CapturePublicationToken
    source_channel_labels: dict[str, str] | None = None


@dataclass(frozen=True)
class SettlingStepResult:
    """Whether rolling owns this step, and whether its episode remains current."""

    handled: bool
    accepted: bool = True
    window_s: float = 10.0
    public_changed: bool = False


@dataclass(frozen=True)
class EpisodeSnapshot:
    """Facts observed before inference; publication revalidates the episode."""

    policy: dict[str, Any]
    words: list[dict[str, Any]]
    frontier_ms: int = 0
    legacy_frontier_ms: int = 0
    trailing_silence_start_ms: int | None = None
    silence_audio_end_ms: int | None = None


def _episode_snapshot(step: SettlingStep) -> EpisodeSnapshot | SettlingStepResult:
    from backend.core.db import get_sync_session
    from backend.models.recording import Recording
    from backend.utils.canonical_pipeline.live_settling import (
        legacy_live_frontier,
        live_settling_frontier,
        load_live_settling_words,
    )

    session = get_sync_session()
    try:
        recording = session.get(Recording, step.recording_id)
        transcript = getattr(recording, "transcript", None)
        state = getattr(transcript, "settling_state", None) or {}
        if (
            transcript is None
            or not state.get("eligible")
            or not hasattr(session, "execute")
        ):
            return SettlingStepResult(False)
        if not publication_is_current(recording, transcript, step.expected_token):
            return SettlingStepResult(True, False)
        policy = {**state["policy"], **current_settling_policy()}
        # A disabled episode belongs to the legacy lane in every phase, so a
        # paused tail is still transcribed as upstream does.
        if policy.get("enabled") is False:
            return SettlingStepResult(False)
        if state.get("phase") != "live":
            return SettlingStepResult(True, False)
        if step.sequence <= state.get("last_step_index", -1):
            return SettlingStepResult(True, window_s=float(policy["window_s"]))
        boundary = step.audio_end_ms - round(float(policy["window_s"]) * 1000)
        words = load_live_settling_words(
            session, recording_id=step.recording_id, window_start_ms=boundary
        )
        return EpisodeSnapshot(
            dict(policy),
            words,
            live_settling_frontier(session, recording_id=step.recording_id),
            legacy_live_frontier(session, recording_id=step.recording_id),
            trailing_silence_start_ms=state.get("trailing_silence_start_ms"),
            silence_audio_end_ms=state.get("silence_audio_end_ms"),
        )
    finally:
        session.close()


def _disable_cpu_episode(step: SettlingStep) -> None:
    from backend.core.db import get_sync_session
    from backend.utils.canonical_pipeline.live_settling import close_live_settling

    session = get_sync_session()
    try:
        transcript, recording = load_capture_publication(session, step.recording_id)
        if publication_is_current(recording, transcript, step.expected_token):
            close_live_settling(session, recording_id=step.recording_id)
            resolve_capture_settling_policy(
                transcript, device="cpu", disable_on_cpu=True
            )
            session.commit()
    finally:
        session.close()


def _speaker_evidence(step: SettlingStep) -> Any:
    if live_speaker_labels_device() is None:
        return None
    from backend.processing.live_diarization.evidence import available_speaker_evidence

    return available_speaker_evidence(step.live_dir, recording_id=step.recording_id)


def _decode_words(
    step: SettlingStep, config: dict[str, Any], audio: Any, input_start_ms: int
) -> tuple[dict[str, Any], str | None]:
    from backend.processing.transcribe import (
        loaded_transcription_device,
        transcribe_audio,
    )
    from backend.processing.vad import silero_vad

    path = step.live_dir / f"settling-{uuid4().hex}.wav"
    try:
        silero_vad.save_audio(str(path), audio.unsqueeze(0), sampling_rate=16000)
        result = transcribe_audio(str(path), config=config)
        device = loaded_transcription_device(config)
        _text, segments = _prepare_live_region(result, 0.0, exact_spans=True)
        channels, _ = rolling_channel_tail(
            step.temp_dir, step.sequence, (step.audio_end_ms - input_start_ms) / 1000
        )
        source_activity = _analyze_live_source_channels(channels)
        payload = _build_live_confidence_payload(
            region_segment_payloads=segments,
            region_start_ms=input_start_ms,
            region_end_ms=step.audio_end_ms,
            source_activity=source_activity,
            source_channel_evidence=_build_live_source_channel_evidence(
                source_activity, step.source_channel_labels or {}
            ),
        )
        for segment in payload["asr_segments"]:
            if segment.get("text") and not segment.get("words"):
                segment["words"] = [
                    {
                        "word": segment["text"],
                        "start_ms": None,
                        "end_ms": None,
                        "source_start": 0,
                        "source_end": len(segment["text"]),
                    }
                ]
        _add_word_sources(
            payload, channels, input_start_ms, step.source_channel_labels or {}
        )
        return payload, device
    finally:
        path.unlink(missing_ok=True)


def _add_word_sources(
    payload: dict[str, Any], channels: Any, input_start_ms: int, labels: dict[str, str]
) -> None:
    """Keep source provenance local to each word as a rolling window crosses sources."""
    for segment in payload["asr_segments"]:
        for word in segment.get("words", []):
            start, end = word.get("start_ms"), word.get("end_ms")
            if start is None or end is None:
                continue
            first = max(0, round((start - input_start_ms) * 16))
            stop = max(first, round((end - input_start_ms) * 16))
            activity = _analyze_live_source_channels(channels[:, first:stop])
            word["source_channel_activity"] = activity
            word["source_channel_evidence"] = _build_live_source_channel_evidence(
                activity, labels
            ).to_payload()


def _publish_step(
    step: SettlingStep,
    snapshot: EpisodeSnapshot,
    payload: dict[str, Any] | None,
    evidence: Any,
) -> SettlingStepResult:
    from backend.core.db import get_sync_session
    from backend.utils.canonical_pipeline.live_settling import (
        SettlingPass,
        expire_live_settling,
        publish_live_settling,
    )

    window_s = float(snapshot.policy["window_s"])
    metadata = {
        key: value for key, value in (payload or {}).items() if key != "asr_segments"
    }
    request = SettlingPass(
        recording_id=step.recording_id,
        expected_token=step.expected_token,
        decode_id=str(
            uuid5(NAMESPACE_URL, f"settling:{step.expected_token}:{step.sequence}")
        ),
        step_index=step.sequence,
        window_start_ms=step.audio_end_ms - round(window_s * 1000),
        audio_end_ms=step.audio_end_ms,
        input_start_ms=metadata.get("utterance_start_ms"),
        metadata=metadata,
        max_segment_ms=snapshot.policy.get("max_segment_ms"),
        line_break_pause_s=snapshot.policy.get(LINE_BREAK_PAUSE_KEY),
        silence_start_ms=_silence_start(step, evidence, snapshot),
    )
    session = get_sync_session()
    try:
        if payload is None:
            publication = expire_live_settling(session, request=request)
        else:
            transcript, recording = load_capture_publication(session, step.recording_id)
            if not publication_is_current(recording, transcript, step.expected_token):
                return SettlingStepResult(True, False, window_s)
            resolve_capture_settling_policy(transcript, device="cuda")
            decide = evidence.decide if evidence is not None else None
            if decide is None and live_speaker_labels_device() is not None:
                decide = lambda _start, _end: LiveLabelDecision(
                    "UNKNOWN", False, None, True
                )
            publication = publish_live_settling(
                session,
                request=request,
                words=[
                    word
                    for segment in payload["asr_segments"]
                    for word in segment.get("words", [])
                ],
                decide_speaker=decide,
            )
        accepted = publication.accepted
        if not accepted:
            # A duplicate step is dropped, but ingestion still advances to the
            # next chunk while its episode stays live.
            transcript, recording = load_capture_publication(session, step.recording_id)
            state = getattr(transcript, "settling_state", None) or {}
            accepted = (
                publication_is_current(recording, transcript, step.expected_token)
                and state.get("phase") == "live"
            )
        session.commit()
        if publication.accepted:
            from backend.utils.canonical_pipeline.live_unknown_metrics import (
                log_covered_unknown_words,
            )

            log_covered_unknown_words(
                step.recording_id, publication.covered_unknown_words, log=logger
            )
        return SettlingStepResult(True, accepted, window_s, bool(publication.changed))
    finally:
        session.close()


def _silence_start(
    step: SettlingStep, evidence: Any, snapshot: EpisodeSnapshot | None = None
) -> int:
    """Retain silence across covered chunks; later speech resets its start."""
    start = round(step.combined_start_s * 1000)
    if (
        snapshot is not None
        and snapshot.trailing_silence_start_ms is not None
        and snapshot.silence_audio_end_ms is not None
        and start <= snapshot.silence_audio_end_ms <= step.audio_end_ms
        and snapshot.trailing_silence_start_ms <= snapshot.silence_audio_end_ms
    ):
        start = snapshot.trailing_silence_start_ms
    ends = [
        round((step.combined_start_s + region["end"]) * 1000)
        for region in step.vad_regions
    ]
    if snapshot is not None:
        # An unaligned atom occupies its observed capture interval; silence
        # cannot close its line before that real interval has ended.
        ends.extend(
            int(word["observed_audio_end_ms"])
            for word in snapshot.words
            if (word.get("start_ms") is None or word.get("end_ms") is None)
            and word.get("observed_audio_end_ms") is not None
        )
    if evidence is not None:
        ends.extend(round(end * 1000) for _start, end in evidence.speech_spans())
    return max([start, *ends])


def run_settling_step(step: SettlingStep, config: dict[str, Any]) -> SettlingStepResult:
    """Decode the latest capture step once; failures retain the accepted hypothesis."""
    import torch

    from backend.processing.transcribe import loaded_transcription_device

    snapshot = _episode_snapshot(step)
    if isinstance(snapshot, SettlingStepResult):
        return snapshot
    snapshot = replace(
        snapshot,
        policy={
            **snapshot.policy,
            "max_segment_ms": round(
                float(config.get("max_segment_s", DEFAULT_MAX_SEGMENT_S)) * 1000
            ),
            LINE_BREAK_PAUSE_KEY: resolve_tuning(config, LINE_BREAK_PAUSE_KEY),
        },
    )
    device = loaded_transcription_device(config)
    if (
        config.get("processing_device") == "cpu"
        or device == "cpu"
        or not torch.cuda.is_available()
    ) and not cpu_settling_allowed():
        _disable_cpu_episode(step)
        return SettlingStepResult(False)
    window_s = float(snapshot.policy["window_s"])
    audio, _ = rolling_audio_tail(step.temp_dir, step.sequence, window_s)
    end_s = step.audio_end_ms / 1000
    available_start_s = end_s - audio.numel() / 16000
    evidence = _speaker_evidence(step)
    vad_spans = [
        (step.combined_start_s + region["start"], step.combined_start_s + region["end"])
        for region in step.vad_regions
    ]
    floor_ms = max(
        step.audio_end_ms - round(window_s * 1000),
        round(available_start_s * 1000),
        snapshot.frontier_ms,
        snapshot.legacy_frontier_ms,
    )
    # Decode while the unlocked window holds speech, including speech the
    # diariser found after VAD missed it, so a turn's last words keep getting
    # comparisons over the next steps instead of waiting for expiry.
    if not chunk_has_speech(
        audio=audio,
        audio_start_s=available_start_s,
        chunk_bounds_s=(floor_ms / 1000, end_s),
        vad_spans=vad_spans,
        diariser_spans=evidence.speech_spans() if evidence is not None else [],
    ):
        return _publish_step(step, snapshot, None, evidence)
    return _decode_step(step, snapshot, config, (audio, available_start_s), evidence)


def _decode_step(
    step: SettlingStep,
    snapshot: EpisodeSnapshot,
    config: dict[str, Any],
    audio_tail: tuple[Any, float],
    evidence: Any,
) -> SettlingStepResult:
    window_s = float(snapshot.policy["window_s"])
    audio, available_start_s = audio_tail
    input_start = plan_settling_window(
        snapshot.words,
        audio_end_ms=step.audio_end_ms,
        window_ms=round(window_s * 1000),
        lead_ms=round(
            min(5.0, max(0.0, float(config.get("context_window_s", 5)))) * 1000
        ),
        frontier_ms=snapshot.frontier_ms,
    )
    input_start = max(
        input_start, round(available_start_s * 1000), snapshot.legacy_frontier_ms
    )
    first = max(0, round((input_start / 1000 - available_start_s) * 16000))
    try:
        payload, device = _decode_words(step, config, audio[first:], input_start)
        if device != "cuda" and not (device == "cpu" and cpu_settling_allowed()):
            if device == "cpu":
                _disable_cpu_episode(step)
            return SettlingStepResult(False)
        config["_settling_device"] = device
        return _publish_step(step, snapshot, payload, evidence)
    except (TranscriptionError, ValueError):
        logger.warning(
            "Retained live words after failed settling decode", exc_info=True
        )
        return _publish_step(step, snapshot, None, evidence)
