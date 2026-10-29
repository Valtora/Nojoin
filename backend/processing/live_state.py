"""State normalization and persistence for the live transcription lane."""

import json
import os
from typing import Any

LIVE_INITIAL_SEQUENCE = 0
_STATE_FILENAME = "state.json"
_STATE_SOURCE_CHANNEL_LABELS_KEY = "source_channel_labels"
_STATE_SEQUENCE_OUTCOMES_KEY = "sequence_outcomes"
_STATE_LIVE_TITLE_ATTEMPTS_KEY = "live_title_attempts"
_MAX_LIVE_SEQUENCE_OUTCOMES = 200
_LIVE_SEQUENCE_OUTCOMES = {"consumed", "skipped", "deferred", "failed"}


def _default_live_state() -> dict:
    return {
        "next_expected": LIVE_INITIAL_SEQUENCE,
        "buffer_abs_start": 0.0,
        # Seconds of audio in live/buffer.wav, for when that file cannot be read.
        "buffer_len_s": None,
        "last_speaker_label": None,
        _STATE_SOURCE_CHANNEL_LABELS_KEY: {},
        _STATE_SEQUENCE_OUTCOMES_KEY: {},
        _STATE_LIVE_TITLE_ATTEMPTS_KEY: 0,
    }


def _sanitize_source_channel_labels(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    labels: dict[str, str] = {}
    for key, label in value.items():
        source_name = str(key or "").strip()
        speaker_label = str(label or "").strip()
        if source_name and speaker_label:
            labels[source_name] = speaker_label
    return labels


def _sanitize_sequence_outcomes(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}

    outcomes: dict[str, dict[str, Any]] = {}
    for raw_sequence, raw_payload in value.items():
        try:
            sequence = int(raw_sequence)
        except (TypeError, ValueError):
            continue
        if sequence < LIVE_INITIAL_SEQUENCE or not isinstance(raw_payload, dict):
            continue
        outcome = str(raw_payload.get("outcome") or "").strip()
        if outcome not in _LIVE_SEQUENCE_OUTCOMES:
            continue
        payload: dict[str, Any] = {"outcome": outcome}
        reason = str(raw_payload.get("reason") or "").strip()
        if reason:
            payload["reason"] = reason
        run = raw_payload.get("run")
        if isinstance(run, list):
            cleaned_run = []
            for item in run:
                try:
                    cleaned_run.append(int(item))
                except (TypeError, ValueError):
                    continue
            if cleaned_run:
                payload["run"] = cleaned_run
        error = str(raw_payload.get("error") or "").strip()
        if error:
            payload["error"] = error[:500]
        outcomes[str(sequence)] = payload

    if len(outcomes) <= _MAX_LIVE_SEQUENCE_OUTCOMES:
        return outcomes

    ordered_keys = sorted(outcomes, key=lambda key: int(key))[
        -_MAX_LIVE_SEQUENCE_OUTCOMES:
    ]
    return {key: outcomes[key] for key in ordered_keys}


def _normalize_live_state(raw_state: dict | None) -> dict:
    default = _default_live_state()
    if not isinstance(raw_state, dict):
        return default

    try:
        next_expected = int(raw_state.get("next_expected", LIVE_INITIAL_SEQUENCE))
    except (TypeError, ValueError):
        next_expected = LIVE_INITIAL_SEQUENCE
    try:
        buffer_abs_start = float(raw_state.get("buffer_abs_start", 0.0))
    except (TypeError, ValueError):
        buffer_abs_start = 0.0
    try:
        buffer_len_s = max(0.0, float(raw_state["buffer_len_s"]))
    except (KeyError, TypeError, ValueError):
        buffer_len_s = None
    try:
        live_title_attempts = int(raw_state.get(_STATE_LIVE_TITLE_ATTEMPTS_KEY, 0))
    except (TypeError, ValueError):
        live_title_attempts = 0

    default.update(
        {
            "next_expected": max(LIVE_INITIAL_SEQUENCE, next_expected),
            "buffer_abs_start": max(0.0, buffer_abs_start),
            "buffer_len_s": buffer_len_s,
            "last_speaker_label": raw_state.get("last_speaker_label") or None,
            _STATE_SOURCE_CHANNEL_LABELS_KEY: _sanitize_source_channel_labels(
                raw_state.get(_STATE_SOURCE_CHANNEL_LABELS_KEY)
            ),
            _STATE_SEQUENCE_OUTCOMES_KEY: _sanitize_sequence_outcomes(
                raw_state.get(_STATE_SEQUENCE_OUTCOMES_KEY)
            ),
            _STATE_LIVE_TITLE_ATTEMPTS_KEY: max(0, live_title_attempts),
        }
    )
    return default


def _record_live_sequence_outcome(
    state: dict,
    *,
    sequence: int,
    outcome: str,
    reason: str,
    run: list[int] | None = None,
    error: str | None = None,
) -> None:
    if outcome not in _LIVE_SEQUENCE_OUTCOMES:
        return
    outcomes = _sanitize_sequence_outcomes(state.get(_STATE_SEQUENCE_OUTCOMES_KEY))
    payload: dict[str, Any] = {"outcome": outcome, "reason": reason}
    if run:
        payload["run"] = [int(item) for item in run]
    if error:
        payload["error"] = str(error).strip()[:500]
    outcomes[str(int(sequence))] = payload
    state[_STATE_SEQUENCE_OUTCOMES_KEY] = _sanitize_sequence_outcomes(outcomes)


def read_live_state(live_dir) -> dict:
    """Read the live lane state, returning defaults when absent or unreadable."""
    state_path = os.path.join(str(live_dir), _STATE_FILENAME)
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _normalize_live_state(data)
    except (FileNotFoundError, TypeError, ValueError, OSError):
        return _default_live_state()


def write_live_state(live_dir, state: dict) -> None:
    """Persist the live lane state to disk."""
    state_path = os.path.join(str(live_dir), _STATE_FILENAME)
    normalized_state = _normalize_live_state(state)
    state_payload = {
        "next_expected": int(normalized_state["next_expected"]),
        "buffer_abs_start": float(normalized_state["buffer_abs_start"]),
    }
    if normalized_state["buffer_len_s"] is not None:
        state_payload["buffer_len_s"] = normalized_state["buffer_len_s"]
    if normalized_state.get("last_speaker_label"):
        state_payload["last_speaker_label"] = normalized_state["last_speaker_label"]
    if normalized_state.get(_STATE_SOURCE_CHANNEL_LABELS_KEY):
        state_payload[_STATE_SOURCE_CHANNEL_LABELS_KEY] = normalized_state[
            _STATE_SOURCE_CHANNEL_LABELS_KEY
        ]
    if normalized_state.get(_STATE_SEQUENCE_OUTCOMES_KEY):
        state_payload[_STATE_SEQUENCE_OUTCOMES_KEY] = normalized_state[
            _STATE_SEQUENCE_OUTCOMES_KEY
        ]
    if normalized_state[_STATE_LIVE_TITLE_ATTEMPTS_KEY]:
        state_payload[_STATE_LIVE_TITLE_ATTEMPTS_KEY] = normalized_state[
            _STATE_LIVE_TITLE_ATTEMPTS_KEY
        ]
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state_payload, f)


def _live_lane_has_consumed(state: dict) -> bool:
    """Whether a live run of this recording has completed (from its state).

    Until one has, each run warns about an unusable tuning value, so the
    warning is not lost when the first run fails before loading the config.
    """
    outcomes = state.get(_STATE_SEQUENCE_OUTCOMES_KEY) or {}
    return any(entry.get("outcome") == "consumed" for entry in outcomes.values())


def claim_live_title_attempt(
    state: dict[str, Any], delay_minutes: int, *, audio_end_s: float
) -> bool:
    """Whether this live publication should ask for the title and gist.

    The first attempt waits for ``delay_minutes`` of capture audio. One that
    fails is retried once, after another delay, rather than on every
    publication. The count lives in the capture's live lane state.
    """
    attempts = int(state.get(_STATE_LIVE_TITLE_ATTEMPTS_KEY) or 0)
    if not delay_minutes or attempts >= 2:
        return False
    if audio_end_s < delay_minutes * 60 * (attempts + 1):
        return False
    state[_STATE_LIVE_TITLE_ATTEMPTS_KEY] = attempts + 1
    return True
