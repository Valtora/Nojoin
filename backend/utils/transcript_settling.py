"""Capture eligibility and immutable provenance for live publication.

Word comparison lives in live_word_settling; this module owns each episode's
mode, the per-step K and window, and the publication boundary shared with
capture lifecycle operations.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict
from uuid import uuid4

from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.utils.capture_resume import durable_resume_state
from backend.utils.config_manager import config_manager
from backend.utils.live_settling_settings import (
    LIVE_SETTLING_DEFAULTS,
    normalise_live_settling_settings,
)


def _capture_policy_settings() -> dict[str, Any]:
    return normalise_live_settling_settings(
        {
            key: config_manager.get(key, default)
            for key, default in LIVE_SETTLING_DEFAULTS.items()
        }
    )


def current_settling_policy() -> dict[str, Any]:
    """K and the window, read at each step so a change applies to the next one."""
    settings = _capture_policy_settings()
    return {
        "k": settings["live_text_settling_k"],
        "window_s": settings["live_text_settling_window_s"],
    }


def cpu_settling_allowed() -> bool:
    """Whether settling may run on a CPU worker (``always``), read at each step."""
    return _capture_policy_settings()["live_text_settling"] == "always"


class SettlingPolicy(TypedDict):
    """Whether the episode settles, pending confirmation of the worker's device."""

    enabled: bool | None


class CaptureSettlingState(TypedDict):
    """Episode runtime state plus durable word-publication provenance."""

    version: int
    eligible: bool
    policy: SettlingPolicy
    generation: str
    capture_epoch: int
    sealed: bool
    phase: str
    last_step_index: NotRequired[int]
    last_decode_id: NotRequired[str]
    speaker_enabled: NotRequired[bool]
    line_break_pause_s: NotRequired[float]
    trailing_silence_start_ms: NotRequired[int | None]
    silence_audio_end_ms: NotRequired[int]
    settled: NotRequired[bool]


@dataclass(frozen=True)
class CapturePublicationToken:
    """The episode whose audio produced a worker's result."""

    capture_epoch: int
    generation: str | None


def durable_settling_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Retain capture geometry and actual word provenance when runtime resets."""
    durable = durable_resume_state(state)
    if (state or {}).get("settled") is not True:
        return durable or None
    durable["settled"] = True
    if state and state.get("name_anchors"):
        durable["name_anchors"] = deepcopy(state["name_anchors"])
    return durable


def new_capture_settling_state(*, capture_epoch: int) -> CaptureSettlingState | None:
    """Mark explicitly enabled new captures; old recordings remain unmarked."""
    settings = _capture_policy_settings()
    if settings["live_text_settling"] == "off" or not config_manager.get(
        "enable_canonical_transcript_writes", True
    ):
        return None
    return {
        "version": 1,
        "eligible": True,
        "policy": {"enabled": None},
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
    if transcript is None or not state or "policy" not in state:
        return
    updated = dict(state)
    if phase == "live" and (
        state.get("phase") != "live" or state.get("capture_epoch") != capture_epoch
    ):
        updated["generation"] = str(uuid4())
        settings = _capture_policy_settings()
        updated["policy"] = {
            "enabled": None
            if settings["live_text_settling"] != "off"
            and config_manager.get("enable_canonical_transcript_writes", True)
            else False,
        }
        updated.pop("last_step_index", None)
        updated.pop("last_decode_id", None)
        updated.pop("speaker_enabled", None)
        updated.pop("trailing_silence_start_ms", None)
        updated.pop("silence_audio_end_ms", None)
    updated.update(capture_epoch=capture_epoch, sealed=sealed, phase=phase)
    transcript.settling_state = updated


def resolve_capture_settling_policy(
    transcript: Transcript | None,
    *,
    device: Literal["cpu", "cuda"] | None,
    disable_on_cpu: bool = False,
) -> None:
    """Freeze effective eligibility only after actual worker placement is known."""
    state = getattr(transcript, "settling_state", None)
    if transcript is None or not state or "policy" not in state or device is None:
        return
    if state["policy"].get("enabled") is not None and not (
        disable_on_cpu and device == "cpu"
    ):
        return
    transcript.settling_state = {
        **state,
        "policy": {
            **state["policy"],
            "enabled": device == "cuda"
            or (device == "cpu" and not disable_on_cpu and cpu_settling_allowed()),
        },
    }
    if device == "cpu":
        transcript.settling_state.pop("trailing_silence_start_ms", None)
        transcript.settling_state.pop("silence_audio_end_ms", None)


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
