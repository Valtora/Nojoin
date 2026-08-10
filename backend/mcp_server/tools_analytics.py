"""MCP access to a meeting's speaking-dynamics analytics."""

import logging
from typing import Any

from fastapi import HTTPException

from backend.mcp_server.auth import get_current_mcp_user
from backend.mcp_server.server import (
    MCP_WRITE_SCOPE,
    _require_write_scope,
    mcp_tool,
)

logger = logging.getLogger(__name__)


def _ms_to_seconds(value: Any) -> float | None:
    if value is None:
        return None
    return round(int(value) / 1000, 1)


def _name_lookup(speakers: list[dict[str, Any]]) -> dict[str, str]:
    return {speaker["speaker_key"]: speaker["name"] for speaker in speakers}


def _speaker_rows(analytics: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per speaker, keyed by display name rather than internal id.

    An assistant should never have to join two collections to answer "who
    talked most", so every per-speaker figure is folded onto one row and the
    internal key is dropped entirely.
    """
    metrics = analytics["metrics"]
    talk_time = metrics["talk_time"]
    turn_structure = metrics["turn_structure"]
    interruptions = metrics["interruptions"]
    latency = metrics["turn_taking"]["response_latency"]

    rows = []
    for speaker in analytics["speakers"]:
        key = speaker["speaker_key"]
        figures = talk_time.get(key, {})
        structure = turn_structure.get(key, {})
        interrupt = interruptions.get(key, {})
        response = latency.get(key, {})
        rows.append(
            {
                "name": speaker["name"],
                "is_named": speaker["is_named"],
                "speaking_seconds": _ms_to_seconds(figures.get("speech_ms", 0)),
                "share_of_speech": figures.get("share_of_speech", 0.0),
                "turn_count": structure.get("turn_count", 0),
                "median_turn_seconds": _ms_to_seconds(structure.get("median_turn_ms")),
                "longest_turn_seconds": _ms_to_seconds(
                    structure.get("longest_turn_ms")
                ),
                "longest_turn_at_seconds": _ms_to_seconds(
                    structure.get("longest_turn_start_ms")
                ),
                "interruptions_made": interrupt.get("made", 0),
                "interruptions_received": interrupt.get("received", 0),
                "median_response_seconds": _ms_to_seconds(response.get("median_ms")),
                "response_sample_count": response.get("sample_count", 0),
            }
        )
    return rows


def _timeline_rows(
    analytics: dict[str, Any], names: dict[str, str]
) -> list[dict[str, Any]]:
    timeline = analytics["metrics"]["timeline"]
    return [
        {
            "start_seconds": _ms_to_seconds(bucket["start_ms"]),
            "end_seconds": _ms_to_seconds(bucket["end_ms"]),
            "speaking_seconds": {
                names.get(key, key): _ms_to_seconds(value)
                for key, value in bucket["speech_ms"].items()
            },
        }
        for bucket in timeline["buckets"]
    ]


def _warning_rows(
    analytics: dict[str, Any], names: dict[str, str]
) -> list[dict[str, Any]] | None:
    warning = analytics.get("attribution_warning")
    if not warning:
        return None
    rows = []
    for reason in warning["reasons"]:
        row = {key: value for key, value in reason.items() if key != "speaker_keys"}
        if "speaker_keys" in reason:
            row["speakers"] = [names.get(key, key) for key in reason["speaker_keys"]]
        rows.append(row)
    return rows


def _delivery_rows(
    stored: dict[str, Any], names: dict[str, str], status: str
) -> dict[str, Any]:
    """The measured delivery tier, keyed by speaker name.

    Reported as a status rather than omitted when absent, so an assistant can
    say "not measured yet" instead of inferring that a meeting had no
    discernible delivery.
    """
    delivery = stored.get("delivery")
    if not delivery or status != "completed":
        return {"status": status, "speakers": {}}

    return {
        "status": "completed",
        # Loudness is only comparable between speakers who came through the
        # same signal chain. Where they did not, this is false, and comparing
        # two speakers' loudness compares codecs rather than people.
        "cross_speaker_loudness_comparable": delivery.get(
            "cross_speaker_loudness_comparable", True
        ),
        "speakers": {
            names.get(key, key): {
                "words_per_minute": figures.get("words_per_minute"),
                "median_pitch_hz": figures.get("median_f0_hz"),
                "pitch_variation_semitones": figures.get("pitch_spread_semitones"),
                "median_loudness_dbfs": figures.get("median_loudness_dbfs"),
                "loudness_range_db": figures.get("loudness_range_db"),
                "within_turn_pauses": figures.get("pause_count"),
                "median_pause_ms": figures.get("median_pause_ms"),
                "measured_from_utterances": figures.get("analysed_utterances"),
            }
            for key, figures in delivery.get("speakers", {}).items()
        },
    }


def _cite_rows(
    citations: list[dict[str, Any]], names: dict[str, str]
) -> list[dict[str, Any]]:
    return [
        {
            "quote": citation.get("quote"),
            "at_seconds": _ms_to_seconds(citation.get("start_ms")),
            "speaker": names.get(citation.get("speaker_key") or "", None),
        }
        for citation in citations or []
    ]


def _ai_topics(block: dict[str, Any], names: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "title": topic.get("title"),
            "start_seconds": _ms_to_seconds(topic.get("start_ms")),
            "end_seconds": _ms_to_seconds(topic.get("end_ms")),
            "summary": topic.get("summary"),
            "led_by": (
                "contested"
                if topic.get("contested")
                else names.get(topic.get("led_by") or "", None)
            ),
            "leadership_basis": topic.get("leadership_basis"),
        }
        for topic in block.get("topics") or []
    ]


def _ai_rows(
    stored: dict[str, Any],
    names: dict[str, str],
    status: str,
    stale: bool,
) -> dict[str, Any]:
    """The AI tier, keyed by speaker name and reported as a status when absent.

    Absent is reported rather than omitted so an assistant says "this has not
    been analysed" instead of treating silence as evidence that a meeting had
    no decisions or no unanswered questions.
    """
    block = stored.get("ai")
    if not block or status != "completed":
        return {"status": status, "topics": [], "sentiment": [], "decisions": []}

    return {
        "status": "completed",
        "generated_at": block.get("computed_at"),
        # True when the transcript has been edited since this ran, so the
        # analysis describes a transcript that no longer exists.
        "stale": stale,
        # True when the meeting was too long to send in full, so the analysis
        # covers only up to analysed_through_seconds.
        "transcript_truncated": bool(block.get("transcript_truncated")),
        "analysed_through_seconds": _ms_to_seconds(block.get("analysed_through_ms")),
        "topics": _ai_topics(block, names),
        "sentiment": [
            {
                "speaker": names.get(item.get("speaker_key") or "", None),
                "tone": item.get("tone"),
                "summary": item.get("summary"),
                "citations": _cite_rows(item.get("citations"), names),
            }
            for item in block.get("sentiment") or []
        ],
        "questions": [
            {
                "question": item.get("question"),
                "asked_by": names.get(item.get("asked_by") or "", None),
                "asked_at_seconds": _ms_to_seconds(item.get("asked_at_ms")),
                "answered_by": names.get(item.get("answered_by") or "", None),
                "answered_at_seconds": _ms_to_seconds(item.get("answered_at_ms")),
                "answer_summary": item.get("answer_summary"),
            }
            for item in block.get("questions") or []
        ],
        "decisions": [
            {
                "decision": item.get("decision"),
                "proposed_by": names.get(item.get("proposed_by") or "", None),
                "agreed_by": [
                    names.get(key, key) for key in item.get("agreed_by") or []
                ],
                "objected_by": [
                    names.get(key, key) for key in item.get("objected_by") or []
                ],
                "consensus": item.get("consensus"),
                "citations": _cite_rows(item.get("citations"), names),
            }
            for item in block.get("decisions") or []
        ],
        # What the evidence rules discarded. Reported so a thin result is
        # distinguishable from a quiet meeting.
        "excluded": block.get("excluded") or {},
    }


@mcp_tool()
async def get_meeting_analytics(recording_id: str) -> dict[str, Any]:
    """Get speaking-dynamics analytics for one meeting.

    Returns who spoke for how long and how the conversation moved between
    people: talk-time share, turn counts, median and longest turn,
    directional interruption counts, median response time, a talk-share
    timeline across the meeting, and totals for silence and overlapping
    speech. Every figure is measured from the transcript's timings, not
    inferred by a model, and matches exactly what the app's Analytics tab
    shows for the same meeting.

    Also returns `delivery` when it has been measured for this recording:
    speaking pace, pitch height and how much it moved, loudness and its
    range, and within-turn pausing. These describe *how* someone spoke and
    make no claim about how they felt -- there is no emotion model here, and
    they must not be relayed as mood, sentiment, or engagement. A fast,
    loud speaker is not necessarily an enthusiastic one. When
    `delivery.status` is not "completed", delivery has not been measured for
    this meeting; say so rather than treating it as absent evidence.

    When `conversation.overlapping_speech_present` is false, this transcript
    contains no overlapping speech anywhere, so every `interruptions_made` and
    `interruptions_received` count is zero because overlap was never
    representable in it -- most often an imported single-channel recording. Say
    that interruptions cannot be measured for this meeting rather than that
    nobody interrupted anyone.

    Three things to respect when using this. Shares are reported against total
    speech rather than against the meeting's duration, so they sum to 1.0
    even when people talked over each other; each speaker's
    share_of_speech is the fraction of all speaking time they held. When
    attribution_warnings is present, speaker attribution for this recording
    may be unreliable -- most often because one person was split into
    several speakers by diarisation -- so say so rather than quoting the
    figures flatly. And when
    `delivery.cross_speaker_loudness_comparable` is false, the speakers
    reached the recording through different signal chains, so comparing their
    loudness compares codecs and microphone gain rather than how loudly they
    actually spoke.

    Also returns `ai_analysis` when the meeting has been analysed by an AI
    model: the topics it moved through and who drove each, a reading of each
    speaker's tone *from their words*, which questions were asked and which
    went unanswered, and who proposed, agreed with, or objected to each
    decision. Unlike everything else here it is a model's reading rather than
    a measurement, so it is generated once on request and never per read. Four
    things govern how to use it. Every sentiment and decision item carries
    timestamped quotes, and items whose quotes could not be found in the
    transcript were discarded before you saw them, so relay the quotes when the
    claim matters. Sentiment is a reading of language and is not fused with
    the measured `delivery` figures, which describe the sound of a voice; do
    not combine them into a single judgement about a person. `consensus` of
    "assumed" means the decision merely went unchallenged, which is not
    agreement. And when `ai_analysis.status` is "unavailable", the deployment
    has no AI provider configured, which is a normal state and not a fault;
    "pending" means nobody has run it yet, and `analyse_meeting` can.

    Only meetings that have finished processing have analytics: a recording
    still capturing or processing has no speaker attribution yet, because
    diarisation runs when the recording is finalised.

    Args:
        recording_id: The recording's string id from list_recordings.
    """
    from mcp.server.fastmcp.exceptions import ToolError

    from backend.api.v1.endpoints.transcripts.helpers import (
        _get_owned_recording,
        _get_recording_transcript,
    )
    from backend.core.db import async_session_maker
    from backend.services.meeting_analytics import compute_recording_analytics
    from backend.utils.canonical_pipeline import (
        ensure_canonical_backfill,
        get_canonical_transcript_revision,
    )

    user = get_current_mcp_user()
    async with async_session_maker() as db:
        recording = await _get_owned_recording(db, recording_id, user.id)
        transcript = await _get_recording_transcript(db, recording.id)
        if transcript is None:
            raise ToolError(
                f"Recording {recording_id} has no transcript yet, so it has no "
                "analytics. Analytics become available once processing finishes."
            )

        def _compute(sync_session):
            ensure_canonical_backfill(sync_session, recording.id)
            return (
                compute_recording_analytics(sync_session, recording),
                get_canonical_transcript_revision(sync_session, recording.id),
            )

        analytics, revision = await db.run_sync(_compute)
        await db.commit()
        stored = transcript.analytics_payload or {}
        delivery_status = transcript.analytics_status
        ai_status = transcript.analytics_ai_status

    ai_watermark = (stored.get("ai") or {}).get("event_watermark")
    ai_stale = ai_watermark is not None and revision > int(ai_watermark)

    metrics = analytics["metrics"]
    names = _name_lookup(analytics["speakers"])

    if not analytics["speakers"]:
        raise ToolError(
            f"Recording {recording_id} has no attributed speech, so there is "
            "nothing to analyse. This is normal for a recording that is still "
            "processing or that captured no speech."
        )

    return {
        "recording_id": recording_id,
        "name": recording.name,
        "duration_seconds": _ms_to_seconds(metrics["duration_ms"]),
        "utterance_count": metrics["utterance_count"],
        "speakers": _speaker_rows(analytics),
        "timeline": _timeline_rows(analytics, names),
        "conversation": {
            "transitions": [
                {
                    "from_speaker": names.get(
                        item["from_speaker"], item["from_speaker"]
                    ),
                    "to_speaker": names.get(item["to_speaker"], item["to_speaker"]),
                    "count": item["count"],
                    "median_latency_seconds": _ms_to_seconds(item["median_latency_ms"]),
                }
                for item in metrics["turn_taking"]["transitions"]
            ],
            "silence_seconds": _ms_to_seconds(metrics["silence"]["silence_ms"]),
            "silence_share": metrics["silence"]["silence_share"],
            "overlapped_seconds": _ms_to_seconds(metrics["overlap"]["overlapped_ms"]),
            "overlap_share": metrics["overlap"]["overlap_share"],
            # False means this transcript holds no overlapping speech at all,
            # so the interruption counts above are not measurements of zero.
            "overlapping_speech_present": metrics["overlap"][
                "overlapping_speech_present"
            ],
        },
        "attribution_warnings": _warning_rows(analytics, names),
        "delivery": _delivery_rows(stored, names, delivery_status),
        "ai_analysis": _ai_rows(stored, names, ai_status, ai_stale),
    }


@mcp_tool(scope=MCP_WRITE_SCOPE)
async def analyse_meeting(recording_id: str) -> dict[str, Any]:
    """Run Nojoin's AI analysis of a meeting's dynamics.

    **This spends the user's AI quota.** Every other write tool in this
    connector makes a free, reversible edit; this one makes a real request to
    the user's configured AI provider, and on a subscription that consumes
    usage they may need later. Call it when the user has asked for the
    analysis, or has asked a question that plainly needs it and the meeting has
    none. Do not call it speculatively, do not call it across several meetings
    to answer one question, and do not call it again to refresh a result that
    is merely stale unless the user asked you to.

    Produces the topics the meeting moved through and who drove each, a reading
    of each speaker's tone from their words, a map of questions to answers, and
    who proposed, agreed with, or objected to each decision. Every sentiment
    and decision claim must carry a verified quote from the transcript or it is
    discarded, so a thin result means thin evidence rather than a quiet
    meeting. Read the result with `get_meeting_analytics`.

    Returns immediately with a status of "generating"; the analysis takes up to
    a few minutes. Poll `get_meeting_analytics` and read `ai_analysis.status`.
    A deployment with no AI provider configured settles at "unavailable", which
    is a normal state and cannot be fixed by calling this again. Requires the
    mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
    """
    from mcp.server.fastmcp.exceptions import ToolError

    from backend.api.v1.endpoints.transcripts.routes_analytics import (
        generate_recording_ai_analytics,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("analysing meetings")

    async with async_session_maker() as db:
        try:
            result = await generate_recording_ai_analytics(
                recording_id, db=db, current_user=user
            )
        except HTTPException as exc:
            raise ToolError(str(exc.detail)) from exc

    return {"id": result.recording_id, "ai_status": result.ai_status}
