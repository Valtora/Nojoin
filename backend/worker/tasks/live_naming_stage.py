"""Live speaker naming: its own step, queued by live publication.

A publication that adds or changes lines queues a check while a live label is
still unnamed (``LivePublicationResult.queue_live_naming``), whatever Meeting
Edge is doing; so does a label write that changes lines, since a line is
usually published before its label exists. At most one check waits per
recording: the dispatcher claims
the slot before queueing and the check frees it as it starts, so lines
published during a check queue the next one.

With the owner's ``suggest_live_speaker_names`` setting off, nothing is
queued and a check already queued does nothing.

The check runs the self-introduction rule, then queues an AI ask when a label
is due one (``refresh_live_speaker_suggestions``) and a provider is set up.
The ask's slot is held until the ask ends, so a second is never queued while
one is in flight. The model is not told what the rule heard: it gets the
finalisation speaker-suggestion prompt over the due labels' lines and their
neighbours, and may leave a label out when it is unsure.
"""

import logging
from collections.abc import Sequence
from typing import Any

from backend.core.single_flight import claim, release_claim
from backend.models.recording import Recording
from backend.models.user import User
from backend.services.timeline_note_service import load_recording_timeline_notes
from backend.utils.llm_config import LLM_PURPOSE_MEETING_EDGE, ResolvedLLMConfig
from backend.utils.meeting_notes import format_segments_for_llm
from backend.utils.speaker_names.live_suggestions import (
    mark_live_asks,
    refresh_live_speaker_suggestions,
    store_live_llm_suggestions,
)
from backend.utils.speaker_names.preferences import (
    live_name_suggestions_enabled,
    owner_settings,
)
from backend.worker.tasks.constants import (
    MEETING_EDGE_TIMEOUT_SECONDS,
    DatabaseTask,
    _llm_backend_from_config,
    celery_app,
    resolve_llm_config,
)

logger = logging.getLogger(__name__)

LIVE_NAMING_TASK = "backend.worker.tasks.refresh_live_speaker_names_task"
LIVE_NAMING_ASK_TASK = "backend.worker.tasks.ask_live_speaker_names_task"
# Frees a slot if its task is lost, as a worker restart can do; an ask's slot
# also covers the call itself.
LIVE_NAMING_QUEUED_TTL_SECONDS = 300
# The latest lines sent, so an ask stays small however long the meeting runs.
ASK_CONTEXT_LINES = 120


def _check_key(recording_id: int) -> str:
    return f"live-naming-queued:{recording_id}"


def _ask_key(recording_id: int) -> str:
    return f"live-naming-ask:{recording_id}"


def _queue(task_name: str, key: str, recording_id: int) -> None:
    """Queue the task unless its slot is held; a dispatch failure is logged."""
    if not claim(key, ttl_seconds=LIVE_NAMING_QUEUED_TTL_SECONDS):
        return
    try:
        celery_app.send_task(task_name, args=[recording_id])
    except Exception as exc:  # noqa: BLE001 -- boundary: dispatch is best-effort
        release_claim(key)
        logger.warning(
            "Failed to queue %s for recording %s: %s", task_name, recording_id, exc
        )


def queue_live_naming_check(recording_id: int) -> None:
    _queue(LIVE_NAMING_TASK, _check_key(recording_id), recording_id)


@celery_app.task(name=LIVE_NAMING_TASK, base=DatabaseTask, bind=True)
def refresh_live_speaker_names_task(self, recording_id: int) -> None:
    """Propose names for unnamed live labels from the current lines."""
    release_claim(_check_key(recording_id))
    recording = self.session.get(Recording, recording_id)
    if recording is None or not live_name_suggestions_enabled(
        owner_settings(self.session, recording.user_id)
    ):
        return
    due = refresh_live_speaker_suggestions(self.session, recording_id)
    if due and _live_llm_config(self.session, recording_id) is not None:
        _queue(LIVE_NAMING_ASK_TASK, _ask_key(recording_id), recording_id)


@celery_app.task(name=LIVE_NAMING_ASK_TASK, base=DatabaseTask, bind=True)
def ask_live_speaker_names_task(self, recording_id: int) -> None:
    """One ask covering every label due one; frees its slot when it ends."""
    try:
        _ask(self.session, recording_id)
    finally:
        release_claim(_ask_key(recording_id))


def _ask(session: Any, recording_id: int) -> None:
    config = _live_llm_config(session, recording_id)
    if config is None:
        return
    user_notes = load_recording_timeline_notes(session, recording_id).user_notes_list()
    # Commits, so no transaction stays open through the call.
    marked = mark_live_asks(session, recording_id)
    if marked is None:
        return
    labels, segments, meeting_context = marked
    result = _llm_backend_from_config(config).infer_speaker_suggestions(
        _ask_transcript(segments, labels),
        timeout=MEETING_EDGE_TIMEOUT_SECONDS,
        user_notes=user_notes,
        meeting_context=meeting_context,
        eligible_labels=labels,
    )
    added = store_live_llm_suggestions(
        session, recording_id, result, provider=config.provider
    )
    logger.info(
        "Live naming asked about %s labels for recording %s, %s proposed",
        len(labels),
        recording_id,
        added,
    )


def _live_llm_config(session: Any, recording_id: int) -> ResolvedLLMConfig | None:
    """The owner's live (Meeting Edge) model, or None without a provider."""
    recording = session.get(Recording, recording_id)
    if recording is None:
        return None
    user = session.get(User, recording.user_id) if recording.user_id else None
    config = resolve_llm_config(
        session,
        (user.settings if user is not None else None) or {},
        purpose=LLM_PURPOSE_MEETING_EDGE,
        user_id=recording.user_id,
    )
    return None if config.missing_configuration_message() else config


def _ask_transcript(segments: Sequence[dict[str, Any]], labels: Sequence[str]) -> str:
    """The labels' lines and the lines either side, where others address them."""
    asked = set(labels)
    lines = [
        segment
        for index, segment in enumerate(segments)
        if str(segment.get("text", "")).strip()
        and any(
            str(neighbour.get("speaker", "")) in asked
            for neighbour in segments[max(index - 1, 0) : index + 2]
        )
    ][-ASK_CONTEXT_LINES:]
    # Lines carry raw labels, the ones the model must answer with.
    return format_segments_for_llm(
        lines,
        {str(line.get("speaker", "")): str(line.get("speaker", "")) for line in lines},
    )


__all__ = [
    "ask_live_speaker_names_task",
    "queue_live_naming_check",
    "refresh_live_speaker_names_task",
]
