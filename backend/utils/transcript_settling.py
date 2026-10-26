"""Capture eligibility and immutable provenance for live publication.

This module stores the policy and episode boundary only. Word comparison and
settling passes belong to the later live projector.
"""

from dataclasses import dataclass
from typing import Any, Literal, TypedDict
from uuid import uuid4

from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.utils.config_manager import config_manager


class SettlingPolicy(TypedDict):
    """Frozen capture policy, pending confirmation of the worker's device."""

    k: int
    window_s: float
    enabled: bool | None


class CaptureSettlingState(TypedDict):
    """Runtime state carried by a settling-capable capture."""

    version: int
    eligible: bool
    policy: SettlingPolicy
    generation: str
    capture_epoch: int
    sealed: bool
    phase: str


@dataclass(frozen=True)
class CapturePublicationToken:
    """The episode whose audio produced a worker's result."""

    capture_epoch: int
    generation: str | None


def new_capture_settling_state(*, capture_epoch: int) -> CaptureSettlingState | None:
    """Mark explicitly enabled new captures; old recordings remain unmarked."""
    if config_manager.get("live_text_settling", "off") != "auto":
        return None
    return {
        "version": 1,
        "eligible": True,
        "policy": {
            "k": max(1, int(config_manager.get("live_text_settling_k", 3))),
            "window_s": max(
                0.1, float(config_manager.get("live_text_settling_window_s", 10))
            ),
            "enabled": None,
        },
        "generation": str(uuid4()),
        "capture_epoch": capture_epoch,
        "sealed": False,
        "phase": "live",
    }


def capture_publication_token(
    recording: Recording, transcript: Transcript | None
) -> CapturePublicationToken:
    """Snapshot provenance before inference, including legacy capture epochs."""
    state = getattr(transcript, "settling_state", None) or {}
    return CapturePublicationToken(
        int(getattr(recording, "capture_epoch", 0)), state.get("generation")
    )


def publication_is_current(
    recording: Recording | None,
    transcript: Transcript | None,
    expected_token: CapturePublicationToken | None,
) -> bool:
    """Check eligibility only after the caller holds the Transcript fence."""
    if recording is None or recording.status not in {
        RecordingStatus.UPLOADING,
        RecordingStatus.PAUSED,
    }:
        return False
    state = getattr(transcript, "settling_state", None)
    if state and state.get("sealed"):
        return False
    return (
        expected_token is None
        or capture_publication_token(recording, transcript) == expected_token
    )


def update_capture_settling_state(
    transcript: Transcript | None, *, capture_epoch: int, sealed: bool, phase: str
) -> None:
    """Publish a lifecycle boundary under the same fence as live results."""
    state = getattr(transcript, "settling_state", None)
    if not state:
        return
    updated = dict(state)
    if phase == "live" and (
        state.get("phase") != "live" or state.get("capture_epoch") != capture_epoch
    ):
        updated["generation"] = str(uuid4())
    updated.update(capture_epoch=capture_epoch, sealed=sealed, phase=phase)
    transcript.settling_state = updated


def resolve_capture_settling_policy(
    transcript: Transcript | None, *, device: Literal["cpu", "cuda"] | None
) -> None:
    """Freeze effective eligibility only after actual worker placement is known."""
    state = getattr(transcript, "settling_state", None)
    if not state or device is None or state["policy"].get("enabled") is not None:
        return
    transcript.settling_state = {
        **state,
        "policy": {**state["policy"], "enabled": device == "cuda"},
    }


def load_capture_publication(
    session: Any, recording_id: int, *, lock: bool = True
) -> tuple[Transcript | None, Recording | None]:
    """Take the fence before rereading eligibility, without locking Recording."""
    from backend.utils.canonical_pipeline.constants import _load_transcript

    if not hasattr(session, "execute"):
        recording = session.get(Recording, recording_id)
        return getattr(recording, "transcript", None), recording
    transcript = _load_transcript(session, recording_id, lock=lock)
    recording = session.get(Recording, recording_id, populate_existing=True)
    return transcript, recording
