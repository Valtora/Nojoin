"""MCP access to a meeting's speaking-dynamics analytics."""

import logging
from typing import Any

from backend.mcp_server.auth import get_current_mcp_user
from backend.mcp_server.server import mcp_tool

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
    from backend.utils.canonical_pipeline import ensure_canonical_backfill

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
            return compute_recording_analytics(sync_session, recording)

        analytics = await db.run_sync(_compute)
        await db.commit()
        stored = transcript.analytics_payload or {}
        delivery_status = transcript.analytics_status

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
    }
