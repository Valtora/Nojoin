"""Fenced rolling word publication without a growing compatibility JSON write."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session
from sqlmodel import col, func, select

from backend.models.pipeline import TranscriptUtterance
from backend.models.transcript import Transcript
from backend.processing.live_limits import DEFAULT_MAX_SEGMENT_S
from backend.processing.processing_tuning import (
    LINE_BREAK_PAUSE_KEY,
    line_break_pause_exceeded,
    resolve_tuning,
)
from backend.utils.config_manager import config_manager
from backend.utils.live_word_settling import (
    close_word_state,
    expire_word_state,
    reconcile_word_state,
)
from backend.utils.transcript_settling import (
    CapturePublicationToken,
    GateChunk,
    load_capture_publication,
    publication_is_current,
)

from .live_settling_boundaries import remap_phrase_end
from .live_settling_projection import (
    SETTLING_KEY,
    DecideSpeaker,
    LineBoundaries,
    PartitionWrite,
    live_rows,
    mutable_row,
    partition_words,
    row_words,
    word_decisions,
    write_partitions,
)


@dataclass(frozen=True)
class SettlingPublicationResult:
    """The accepted transaction's bounded canonical mutations."""

    accepted: bool
    changed: int = 0
    tombstones: tuple[str, ...] = ()
    new_public_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SettlingPass:
    """Immutable audio and publication provenance for one capture chunk step."""

    recording_id: int
    expected_token: CapturePublicationToken
    decode_id: str
    step_index: int
    window_start_ms: int
    audio_end_ms: int
    input_start_ms: int | None = None
    metadata: dict[str, Any] | None = None
    gate_chunks: list[GateChunk] | None = None
    max_segment_ms: int | None = None
    silence_start_ms: int | None = None
    line_break_pause_s: float | None = None


@dataclass(frozen=True)
class _Projection:
    transcript: Transcript
    rows: list[TranscriptUtterance]
    decide: DecideSpeaker | None = None
    closing: bool = False
    metadata: dict[str, Any] | None = None
    decode_id: str | None = None
    silence_start_ms: int | None = None


def settling_enabled(transcript: Transcript | None) -> bool:
    """Only an explicitly marked, resolved CUDA episode uses the new lane."""
    state = getattr(transcript, "settling_state", None) or {}
    return bool(
        state.get("eligible") and state.get("policy", {}).get("enabled") is True
    )


def load_live_settling_words(
    session: Session, *, recording_id: int, window_start_ms: int = 0
) -> list[dict[str, Any]]:
    """Snapshot the mutable word tail and nearby immutable lead-up anchors."""
    return [
        word
        for row in live_rows(session, recording_id, window_start_ms=window_start_ms)
        for word in row_words(row)
    ]


def live_settling_frontier(session: Session, *, recording_id: int) -> int:
    """Return the immutable audio frontier, including prior CPU whole-line output."""
    payload = col(TranscriptUtterance.confidence_payload)
    return int(
        session.execute(
            select(func.max(TranscriptUtterance.end_ms))
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(TranscriptUtterance.state == "provisional")
            .where(TranscriptUtterance.source_kind == "live")
            .where(
                payload[SETTLING_KEY]["version"].as_integer().is_(None)
                | payload[SETTLING_KEY]["text_locked"].as_boolean().is_(True)
            )
        ).scalar_one_or_none()
        or 0
    )


def legacy_live_frontier(session: Session, *, recording_id: int) -> int:
    """Keep prior whole-line CPU audio out of a resumed rolling CUDA decode."""
    return int(
        session.execute(
            select(func.max(TranscriptUtterance.end_ms))
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(TranscriptUtterance.state == "provisional")
            .where(TranscriptUtterance.source_kind == "live")
            .where(
                col(TranscriptUtterance.confidence_payload)[SETTLING_KEY]["version"]
                .as_integer()
                .is_(None)
            )
        ).scalar_one_or_none()
        or 0
    )


def _speaker_enabled() -> bool:
    """Whether live labels run, so a line's lock waits for its speaker's.

    Not merely requested: on a CPU worker labels never run, and settling there
    (``always``) must still lock lines.
    """
    from backend.processing.live_diarization.step import live_speaker_labels_device

    return live_speaker_labels_device() is not None


def _line_boundaries(
    rows: list[TranscriptUtterance],
    state: dict[str, Any],
    silence_start_ms: int | None,
    current: list[dict[str, Any]],
) -> LineBoundaries:
    """Attach confirmed ends to revised phrase spans before considering timing."""
    closed_ends: dict[str, int | None] = {}
    cutoffs: set[int] = set()
    previous_end = -1
    phrase_words: list[dict[str, Any]] = []
    for row in rows:
        phrase_words.extend(row_words(row))
        stored = (row.confidence_payload or {}).get(SETTLING_KEY, {})
        if (
            not stored.get("boundary_locked")
            or stored.get("closure_reason") == "speaker"
        ):
            continue
        point = stored.get("closed_at_ms", row.end_ms)
        end = remap_phrase_end(phrase_words, current, after_index=previous_end)
        if end is not None:
            closed_ends[current[end]["id"]] = point
            previous_end = end
        elif point is not None:
            cutoffs.add(point)
        phrase_words = []
    return LineBoundaries(
        max_segment_ms=state.get(
            "max_segment_ms",
            round(
                config_manager.get("live_max_segment_s", DEFAULT_MAX_SEGMENT_S) * 1000
            ),
        ),
        closed_ends=closed_ends,
        closed_at_ms=sorted(cutoffs),
        silence_start_ms=silence_start_ms,
        line_break_pause_s=resolve_tuning(state, LINE_BREAK_PAUSE_KEY),
    )


def _project(
    session: Session,
    context: _Projection,
    words: list[dict[str, Any]],
) -> SettlingPublicationResult:
    transcript, rows = context.transcript, context.rows
    mutable = [row for row in rows if mutable_row(row)]
    immutable_ids = {
        word.get("id")
        for row in rows
        if not mutable_row(row)
        for word in row_words(row)
    }
    words = [word for word in words if word.get("id") not in immutable_ids]
    state = transcript.settling_state or {}
    speaker_enabled = state.get("speaker_enabled", _speaker_enabled())
    decisions = word_decisions(
        words,
        mutable,
        context.decide,
        speaker_enabled=speaker_enabled,
        closing=context.closing,
    )
    partitions = partition_words(
        words,
        decisions,
        closing=context.closing,
        barriers=[row.start_ms for row in rows if not mutable_row(row)],
        boundaries=_line_boundaries(mutable, state, context.silence_start_ms, words),
    )
    tombstones, ids, changed = write_partitions(
        session,
        PartitionWrite(
            transcript.recording_id,
            mutable,
            state["generation"],
            context.metadata or {},
            context.decode_id,
        ),
        partitions,
    )
    if changed and partitions and state.get("settled") is not True:
        # Recording-level provenance outlives the first final's line metadata.
        transcript.settling_state = {**state, "settled": True}
        session.add(transcript)
    return SettlingPublicationResult(True, changed, tuple(tombstones), tuple(ids))


def _fresh(
    transcript: Transcript | None,
    recording: Any,
    request: SettlingPass,
    *,
    allow_unresolved: bool = False,
) -> bool:
    if transcript is None:
        return False
    state = transcript.settling_state or {}
    return (
        (
            settling_enabled(transcript)
            or (
                allow_unresolved
                and bool(state.get("eligible"))
                and state.get("policy", {}).get("enabled") is None
            )
        )
        and publication_is_current(recording, transcript, request.expected_token)
        and state.get("phase") == "live"
        and request.step_index > state.get("last_step_index", -1)
    )


def _unprotected_hypothesis(
    words: list[dict[str, Any]], protected: list[TranscriptUtterance], frontier: int
) -> list[dict[str, Any]]:
    """Remove protected context together with its unplaced word neighbours."""
    kept: list[bool | None] = []
    for word in words:
        start = word.get("start_ms")
        if start is None:
            kept.append(None)
        else:
            kept.append(
                word.get("end_ms", 0) > frontier
                and not any(
                    row.start_ms <= start < row.end_ms
                    for row in protected
                    if row.manual_text_locked or row.manual_speaker_locked
                )
            )
    first = next((item for item in kept if item is not None), True)
    previous = first
    for index, flag in enumerate(kept):
        if flag is None:
            kept[index] = previous
        else:
            previous = flag
    return [word for word, keep in zip(words, kept, strict=True) if keep]


def publish_live_settling(
    session: Session,
    *,
    request: SettlingPass,
    words: list[dict[str, Any]],
    decide_speaker: DecideSpeaker | None = None,
) -> SettlingPublicationResult:
    """Accept one fresh observation and publish its complete word partition atomically."""
    transcript, recording = load_capture_publication(session, request.recording_id)
    if transcript is None or not _fresh(
        transcript,
        recording,
        request,
    ):
        return SettlingPublicationResult(False)
    state = transcript.settling_state or {}
    if state.get("last_decode_id") == request.decode_id:
        return SettlingPublicationResult(False)
    rows = live_rows(
        session,
        request.recording_id,
        window_start_ms=request.window_start_ms,
        lock=True,
    )
    existing = [word for row in rows for word in row_words(row)]
    protected = [row for row in rows if not mutable_row(row)]
    frontier = live_settling_frontier(session, recording_id=request.recording_id)
    hypothesis = _unprotected_hypothesis(words, protected, frontier)
    # Observation bounds describe capture coverage, never invented word times.
    hypothesis = [
        {
            **word,
            "observed_audio_start_ms": request.input_start_ms
            if request.input_start_ms is not None
            else max(0, request.window_start_ms),
        }
        if word.get("start_ms") is None
        else word
        for word in hypothesis
    ]
    settled = reconcile_word_state(
        existing,
        hypothesis,
        k=state["policy"]["k"],
        decode_id=request.decode_id,
        window_start_ms=request.window_start_ms,
        audio_end_ms=request.audio_end_ms,
        input_start_ms=request.input_start_ms,
    )
    state = {
        **state,
        "speaker_enabled": state.get("speaker_enabled", _speaker_enabled()),
        "trailing_silence_start_ms": request.silence_start_ms,
        "silence_audio_end_ms": request.audio_end_ms,
        **(
            {"max_segment_ms": request.max_segment_ms}
            if request.max_segment_ms is not None
            else {}
        ),
        **(
            {
                LINE_BREAK_PAUSE_KEY: resolve_tuning(
                    {LINE_BREAK_PAUSE_KEY: request.line_break_pause_s},
                    LINE_BREAK_PAUSE_KEY,
                )
            }
            if request.line_break_pause_s is not None
            else {}
        ),
    }
    transcript.settling_state = state
    metadata = {
        **(request.metadata or {}),
        "utterance_start_ms": request.input_start_ms
        if request.input_start_ms is not None
        else max(0, request.window_start_ms),
        "utterance_end_ms": request.audio_end_ms,
    }
    result = _project(
        session,
        _Projection(
            transcript,
            rows,
            decide_speaker,
            metadata=metadata,
            decode_id=request.decode_id,
            silence_start_ms=_confirmed_silence(request, state),
        ),
        settled,
    )
    transcript.settling_state = {
        **(transcript.settling_state or {}),
        "last_step_index": request.step_index,
        "last_decode_id": request.decode_id,
        **(
            {"gate_chunks": request.gate_chunks}
            if request.gate_chunks is not None
            else {}
        ),
    }
    session.add(transcript)
    return result


def _confirmed_silence(request: SettlingPass, state: dict[str, Any]) -> int | None:
    """A short ASR hypothesis alone is never evidence of trailing silence."""
    start = request.silence_start_ms
    if start is not None and line_break_pause_exceeded(
        (request.audio_end_ms - start) / 1000, state
    ):
        return start
    return None


def expire_live_settling(
    session: Session,
    *,
    request: SettlingPass,
) -> SettlingPublicationResult:
    """Advance expiry on a speech-free step without creating an ASR observation."""
    transcript, recording = load_capture_publication(session, request.recording_id)
    if transcript is None or not _fresh(
        transcript,
        recording,
        request,
        allow_unresolved=request.gate_chunks is not None,
    ):
        return SettlingPublicationResult(False)
    if not settling_enabled(transcript):
        transcript.settling_state = {
            **(transcript.settling_state or {}),
            "last_step_index": request.step_index,
            "gate_chunks": request.gate_chunks,
            "trailing_silence_start_ms": request.silence_start_ms,
            "silence_audio_end_ms": request.audio_end_ms,
        }
        session.add(transcript)
        return SettlingPublicationResult(True)
    state = {
        **(transcript.settling_state or {}),
        "trailing_silence_start_ms": request.silence_start_ms,
        "silence_audio_end_ms": request.audio_end_ms,
    }
    if request.line_break_pause_s is not None:
        state = {
            **state,
            LINE_BREAK_PAUSE_KEY: resolve_tuning(
                {LINE_BREAK_PAUSE_KEY: request.line_break_pause_s},
                LINE_BREAK_PAUSE_KEY,
            ),
        }
    transcript.settling_state = state
    rows = live_rows(
        session,
        request.recording_id,
        window_start_ms=request.window_start_ms,
        lock=True,
    )
    words = expire_word_state(
        [word for row in rows for word in row_words(row)],
        window_start_ms=request.window_start_ms,
    )
    result = _project(
        session,
        _Projection(
            transcript, rows, silence_start_ms=_confirmed_silence(request, state)
        ),
        words,
    )
    transcript.settling_state = {
        **(transcript.settling_state or {}),
        "last_step_index": request.step_index,
        **(
            {"gate_chunks": request.gate_chunks}
            if request.gate_chunks is not None
            else {}
        ),
    }
    session.add(transcript)
    return result


def project_live_settling_speakers(
    session: Session, transcript: Transcript, decide: DecideSpeaker
) -> tuple[SettlingPublicationResult, int, float | None]:
    """Repartition marked mutable lines per word, independent of the legacy cursor."""
    rows = live_rows(session, transcript.recording_id, lock=True)
    result = _project(
        session,
        _Projection(transcript, rows, decide),
        [word for row in rows for word in row_words(row)],
    )
    current = live_rows(session, transcript.recording_id)
    open_rows = [row for row in current if mutable_row(row)]
    return (
        result,
        min((row.start_ms for row in open_rows), default=0),
        min((row.start_ms / 1000 for row in open_rows), default=None),
    )


def close_live_settling(session: Session, *, recording_id: int) -> bool:
    """Lock the last accepted hypothesis and materialise one compatibility snapshot."""
    from .core import refresh_transcript_projection_from_canonical

    transcript, _ = load_capture_publication(session, recording_id)
    if transcript is None or not transcript.settling_state:
        return False
    rows = live_rows(session, recording_id, lock=True)
    words = close_word_state([word for row in rows for word in row_words(row)])
    _project(session, _Projection(transcript, rows, closing=True), words)
    refresh_transcript_projection_from_canonical(session, recording_id)
    return True
