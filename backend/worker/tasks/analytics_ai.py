"""AI analytics task: topics, sentiment, questions, and decision ownership.

Network-bound, so it runs on the **IO lane** beside the other LLM work rather
than on the GPU lane finalise holds, matching how finalise already hands
meeting intelligence to that lane for non-local providers.

It is never dispatched automatically. Producing this tier spends the user's own
AI quota, and unlike notes it answers a question most meetings are never asked,
so it runs when someone asks for it: the Analytics tab's button, or the MCP
``analyse_meeting`` tool. That is also why a missing AI provider settles as
``unavailable`` rather than ``error`` -- an install with no provider is working
correctly, and the other two analytics tiers are unaffected.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from sqlmodel import Session, select

from backend.celery_app import celery_app
from backend.core.db import get_sync_session
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.processing.pipeline_metrics import pipeline_metric_timer
from backend.utils.analytics_payload import AI_KEY, merge_analytics_payload
from backend.utils.live_speaker_labels import speaker_display_name
from backend.utils.meeting_analysis import (
    MEETING_ANALYSIS_METHOD_VERSION,
    MeetingAnalysisRequest,
    build_quote_index,
    build_speaker_allowlist,
    serialize_meeting_analysis_result,
)
from backend.utils.output_snapshot import capture_output_snapshot
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)

# Wall-clock ceiling for the provider call. Generous because this is a single
# long structured generation over a whole meeting, and a slow local model on a
# CPU-only Ollama host is a supported configuration rather than a fault.
MEETING_ANALYSIS_TIMEOUT_SECONDS = 600

# Transcript characters sent to the model. A very long meeting is truncated
# rather than refused, and the truncation is recorded on the payload so the
# interface can say the analysis covers only part of the meeting. Silently
# analysing the first two hours of a four-hour meeting and presenting the
# result as the meeting is exactly the kind of quiet wrongness this surface
# must not produce.
MEETING_ANALYSIS_MAX_TRANSCRIPT_CHARS = 240_000

AI_STATUS_UNAVAILABLE = "unavailable"
AI_STATUS_GENERATING = "generating"
AI_STATUS_COMPLETED = "completed"
AI_STATUS_ERROR = "error"


def _format_line(start_ms: int, end_ms: int, name: str, text: str) -> str:
    start = max(int(start_ms), 0) // 1000
    end = max(int(end_ms), 0) // 1000
    return (
        f"[{start // 60:02d}:{start % 60:02d} - {end // 60:02d}:{end % 60:02d}] "
        f"{name}: {text}"
    )


def _build_analysis_inputs(session: Session, recording: Recording) -> dict[str, Any]:
    """Derive names, evidence, and revision from one live-compatible snapshot."""
    snapshot = capture_output_snapshot(session, recording.id)
    segments = snapshot.segments
    visible = [
        segment
        for segment in segments
        if segment.get("speaker") not in (None, "UNKNOWN")
        or segment.get("provisional") is True
    ]
    segments = visible or segments
    names: dict[str, str] = {}
    lines: list[str] = []
    texts: list[str] = []
    used_chars = 0
    truncated = False
    covered_ms = 0

    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        speaker_id = segment.get("recording_speaker_id")
        label = str(segment.get("speaker") or "UNKNOWN")
        key = f"rs:{speaker_id}" if speaker_id is not None else f"label:{label}"
        name = snapshot.speaker_map.get(key) or speaker_display_name(
            label, snapshot.speaker_map
        )
        line = _format_line(
            int(float(segment.get("start") or 0) * 1000),
            int(float(segment.get("end") or 0) * 1000),
            name,
            text,
        )
        if used_chars + len(line) + 1 > MEETING_ANALYSIS_MAX_TRANSCRIPT_CHARS and lines:
            truncated = True
            break
        lines.append(line)
        names[key] = name
        texts.append(text)
        used_chars += len(line) + 1
        covered_ms = max(covered_ms, int(float(segment.get("end") or 0) * 1000))

    duration_seconds = max(float(recording.duration_seconds or 0), covered_ms / 1000)
    return {
        "allowlist": build_speaker_allowlist(
            [{"speaker_key": key, "name": name} for key, name in names.items()]
        ),
        "transcript": "\n".join(lines),
        # Bounded by what was actually sent, so a quote from a truncated tail
        # cannot verify against text the model never saw.
        "quotes": build_quote_index(texts, duration_seconds),
        "truncated": truncated,
        "covered_ms": covered_ms,
        "speaker_count": len(names),
        "event_watermark": snapshot.event_watermark,
    }


def _locked_transcript(session: Session, recording_id: int) -> Transcript | None:
    return session.exec(
        select(Transcript)
        .where(Transcript.recording_id == recording_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def _claim_ai_generation(
    session: Session, recording_id: int, request_id: str | None
) -> str | None:
    transcript = _locked_transcript(session, recording_id)
    if transcript is None:
        return None
    generation = (transcript.analytics_payload or {}).get("ai_generation") or {}
    if request_id is not None:
        claimed = (
            generation.get("request_id") == request_id
            and transcript.analytics_ai_status == "generating"
        )
        session.commit()
        return request_id if claimed else None
    if transcript.analytics_ai_status == "generating":
        session.commit()
        return None
    request_id = str(uuid4())
    transcript.analytics_payload = merge_analytics_payload(
        transcript.analytics_payload, {"ai_generation": {"request_id": request_id}}
    )
    transcript.analytics_ai_status = AI_STATUS_GENERATING
    transcript.analytics_ai_error_message = None
    session.add(transcript)
    session.commit()
    return request_id


def _set_ai_status(
    session: Session,
    recording_id: int,
    status: str,
    message: str | None = None,
    *,
    request_id: str,
) -> None:
    transcript = _owned_transcript(session, recording_id, request_id)
    if transcript is None:
        return
    transcript.analytics_ai_status = status
    transcript.analytics_ai_error_message = message
    session.add(transcript)
    session.commit()


def _owned_transcript(
    session: Session, recording_id: int, request_id: str
) -> Transcript | None:
    transcript = _locked_transcript(session, recording_id)
    generation = (
        (transcript.analytics_payload or {}).get("ai_generation")
        if transcript
        else None
    )
    if (
        not generation
        or generation.get("request_id") != request_id
        or transcript.analytics_ai_status != AI_STATUS_GENERATING
    ):
        session.commit()
        return None
    return transcript


def _record_ai_snapshot(
    session: Session, recording_id: int, request_id: str, inputs: dict[str, Any]
) -> bool:
    transcript = _owned_transcript(session, recording_id, request_id)
    if transcript is None:
        return False
    transcript.analytics_payload = merge_analytics_payload(
        transcript.analytics_payload,
        {
            "ai_generation": {
                "request_id": request_id,
                "event_watermark": inputs["event_watermark"],
                "generated_through_ms": inputs["covered_ms"],
            },
        },
    )
    session.add(transcript)
    session.commit()
    return True


def _store_ai_payload(
    session: Session, recording_id: int, block: dict[str, Any], *, request_id: str
) -> None:
    """Write the AI block without disturbing the measured delivery block.

    The row is locked for the read-modify-write because the delivery task
    writes the same column from a different lane, and an unlocked pair would
    let one tier's result vanish behind the other's.
    """
    from sqlalchemy.orm.attributes import flag_modified

    transcript = _owned_transcript(session, recording_id, request_id)
    if transcript is None:
        return
    transcript.analytics_payload = merge_analytics_payload(
        transcript.analytics_payload,
        {
            AI_KEY: block,
            "ai_generation": {
                "request_id": request_id,
                "event_watermark": block["event_watermark"],
                "generated_through_ms": block["analysed_through_ms"],
            },
        },
    )
    flag_modified(transcript, "analytics_payload")
    transcript.analytics_ai_status = AI_STATUS_COMPLETED
    transcript.analytics_ai_error_message = None
    session.add(transcript)
    session.commit()


def _resolve_backend(session, recording):
    """The provider chain for this recording's owner, or a reason there is none."""
    from backend.models.user import User
    from backend.processing.llm_services import get_llm_backend_with_secondary
    from backend.utils.languages import (
        apply_recording_language,
        resolve_language_preferences,
    )
    from backend.utils.llm_config import resolve_llm_config

    user_settings: dict[str, Any] = {}
    if recording.user_id:
        user = session.get(User, recording.user_id)
        if user and user.settings:
            user_settings = user.settings

    llm_config = resolve_llm_config(session, user_settings, user_id=recording.user_id)
    missing = llm_config.missing_configuration_message()
    if missing:
        return None, None, missing

    preferences = resolve_language_preferences(
        apply_recording_language(llm_config.merged_config, recording),
        transcription_backend=llm_config.merged_config.get("transcription_backend"),
    )
    return (
        get_llm_backend_with_secondary(llm_config),
        preferences.notes_language_instruction,
        None,
    )


@celery_app.task(name="backend.worker.tasks.compute_meeting_analysis_task")
def compute_meeting_analysis_task(
    recording_id: int, request_id: str | None = None
) -> dict[str, Any]:
    """Generate and persist the AI analytics tier for one recording."""
    with get_sync_session() as session:
        recording = session.get(Recording, recording_id)
        if recording is None:
            return {"status": "skipped", "reason": "recording_not_found"}

        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).first()
        if transcript is None:
            return {"status": "skipped", "reason": "transcript_not_found"}

        request_id = _claim_ai_generation(session, recording_id, request_id)
        if request_id is None:
            return {"status": "skipped", "reason": "generation_superseded"}

        try:
            backend, language_instruction, missing = _resolve_backend(
                session, recording
            )
            if missing:
                _set_ai_status(
                    session,
                    recording_id,
                    AI_STATUS_UNAVAILABLE,
                    missing,
                    request_id=request_id,
                )
                return {"status": "unavailable", "reason": missing}
            inputs = _build_analysis_inputs(session, recording)
            if not inputs["allowlist"].names or not inputs["transcript"]:
                _set_ai_status(
                    session,
                    recording_id,
                    AI_STATUS_ERROR,
                    "This meeting has no attributed speech to analyse.",
                    request_id=request_id,
                )
                return {"status": "skipped", "reason": "no_attributed_speech"}
            if not _record_ai_snapshot(session, recording_id, request_id, inputs):
                return {"status": "skipped", "reason": "generation_superseded"}
            request = MeetingAnalysisRequest(
                transcript=inputs["transcript"],
                allowlist=inputs["allowlist"],
                quotes=inputs["quotes"],
                output_language_instruction=language_instruction,
            )
            with pipeline_metric_timer(
                stage="meeting_analysis",
                recording_id=recording_id,
                payload={"speaker_count": inputs["speaker_count"]},
                log=logger,
            ) as metric:
                result = backend.generate_meeting_analysis(
                    request,
                    timeout=MEETING_ANALYSIS_TIMEOUT_SECONDS,
                )
                metric["payload"].update(
                    {
                        "topics": len(result.topics),
                        "sentiment": len(result.sentiment),
                        "questions": len(result.questions),
                        "decisions": len(result.decisions),
                        "excluded": result.excluded.total,
                    }
                )
        except Exception as exc:  # noqa: BLE001 -- boundary: analytics must never fail a meeting
            logger.warning(
                "Meeting analysis failed for recording %s: %s",
                recording_id,
                exc,
                exc_info=True,
            )
            session.rollback()
            # Written after the rollback so a run that died mid-generation can
            # never be left reading as still generating.
            _set_ai_status(
                session,
                recording_id,
                AI_STATUS_ERROR,
                "This meeting could not be analysed.",
                request_id=request_id,
            )
            return {"status": "error", "recording_id": recording_id}

        block = serialize_meeting_analysis_result(result)
        block.update(
            {
                "method_version": MEETING_ANALYSIS_METHOD_VERSION,
                "computed_at": utc_now().isoformat(),
                # The cursor this was analysed against. A later cursor means the
                # transcript moved underneath it, which the interface reports as
                # stale rather than regenerating: another LLM call is the user's
                # quota to spend.
                "event_watermark": inputs["event_watermark"],
                "transcript_truncated": inputs["truncated"],
                "analysed_through_ms": inputs["covered_ms"],
            }
        )
        _store_ai_payload(session, recording_id, block, request_id=request_id)

    return {"status": "success", "recording_id": recording_id}
