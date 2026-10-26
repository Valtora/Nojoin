"""One atomic numeric envelope for shadow identities and native live state.

Identity validation precedes UnitBank construction, which can truncate rows.
Native-only damage retains the shadow namespace and permanently hands back.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import numpy as np

from backend.processing.nemotron_types import NemotronStateError

from . import native_state, state
from .engine import LiveLabelConfig
from .files import STATE_FILENAME

if TYPE_CHECKING:
    from .hybrid import HybridLiveState

HYBRID_VERSION = 2
_INTS = (
    "consumed_n",
    "packet_cursor_n",
    "native_complete_frame",
    "posterior_first_frame",
    "next_display_id",
    "recording_id",
    "source_sample_offset",
)
_STRINGS = (
    "speaker_engine",
    "reason",
    "asset_fingerprint",
    "runtime_fingerprint",
    "route",
    "source_tail_sha256",
)
_ARRAYS = ("pending", "channel_frames", "posteriors", "overlap", "display_labels")


def save_hybrid(
    live_dir: Path, engine: HybridLiveState, *, filename: str = STATE_FILENAME
) -> None:
    """Durably save consumed decisions before any label transaction publishes them."""
    meta, arrays = state.encode_shadow(engine.shadow)
    generation = getattr(engine, "capture_generation", None) or str(uuid4())
    engine.capture_generation = generation
    hybrid = {
        "version": HYBRID_VERSION,
        "capture_generation": generation,
        **{name: getattr(engine, name, 0) for name in _INTS},
        **{name: getattr(engine, name) for name in _STRINGS},
        "source_sequence": engine.source_sequence,
        "used_eighth_channel": engine.used_eighth_channel,
        "switch_emitted_at_n": engine.switch_emitted_at_n,
        "overlap_until_s": engine.overlap_until_s,
        "translation": sorted(engine.translation.items()),
        "native": None,
    }
    for name in _ARRAYS:
        arrays[f"hybrid_{name}"] = getattr(engine, name).copy()
    if engine.stream is not None:
        hybrid["native"], native_arrays = native_state.encode_stream(engine.stream)
        arrays.update(native_arrays)
    meta["hybrid"] = hybrid
    _validate_identity(hybrid, arrays, engine.recording_id)
    state._validate(live_dir, engine.config, meta, arrays)
    state.write_snapshot(live_dir, meta, arrays, filename=filename)


def load_hybrid(
    live_dir: Path,
    config: LiveLabelConfig,
    *,
    recording_id: int,
    filename: str = STATE_FILENAME,
) -> HybridLiveState | None:
    """Restore exact state or a translated shadow, never invented identities."""
    try:
        snapshot = state.read_snapshot(live_dir, config, filename=filename)
        if snapshot is None:
            return None
        meta, arrays = snapshot
        state._validate(live_dir, config, meta, arrays)
        hybrid = meta.get("hybrid")
        if hybrid is None:
            return _legacy(live_dir, config, meta, arrays, recording_id)
        _validate_identity(hybrid, arrays, recording_id)
        stream, native_failed = _decode_native(hybrid, arrays)
        # Only here may UnitBank remove uncommitted append rows.
        shadow = state._restore(live_dir, config, meta, arrays)
        engine = _assemble(shadow, hybrid, arrays, stream)
        if native_failed:
            engine.fallback("state_incompatible", engine.packet_cursor_n)
        return engine
    except (*state._UNREADABLE, NemotronStateError) as exc:
        raise state.LiveLabelStateError(f"Unreadable hybrid state: {exc!r}") from exc


def _validate_identity(
    meta: dict[str, Any],
    arrays: dict[str, np.ndarray],
    recording_id: int,
) -> None:
    state._check(
        isinstance(meta, dict) and meta.get("version") == HYBRID_VERSION,
        "hybrid version",
    )
    state._check(meta["recording_id"] == recording_id, "recording identity")
    state._check(isinstance(meta["capture_generation"], str), "capture generation")
    UUID(meta["capture_generation"])
    for name in _INTS:
        state._check(type(meta[name]) is int and meta[name] >= 0, name)
    state._check(
        type(meta["source_sequence"]) is int and meta["source_sequence"] >= -1,
        "source sequence",
    )
    state._check(meta["speaker_engine"] in {"auto", "classic"}, "speaker engine")
    state._check(meta["route"] in {"nemotron", "clustering"}, "route")
    state._check(
        meta["reason"] is None or isinstance(meta["reason"], str), "route reason"
    )
    state._check(type(meta["used_eighth_channel"]) is bool, "eighth-channel latch")
    switch = meta["switch_emitted_at_n"]
    state._check(
        switch is None or (type(switch) is int and 0 <= switch <= meta["consumed_n"]),
        "switch cursor",
    )
    state._check(
        np.isfinite(meta["overlap_until_s"]) and meta["overlap_until_s"] >= 0,
        "overlap cursor",
    )
    for name in ("asset_fingerprint", "runtime_fingerprint", "source_tail_sha256"):
        state._check(isinstance(meta[name], str), name)
    _validate_projection(meta, arrays)
    _validate_translation(meta, arrays)
    _validate_source(meta, arrays)


def _validate_projection(meta: dict[str, Any], arrays: dict[str, np.ndarray]) -> None:
    pending = arrays["hybrid_pending"]
    state._check(
        pending.ndim == 1 and pending.dtype == np.float32, "hybrid partial packet"
    )
    state._check(
        len(pending) < 32000 and bool(np.isfinite(pending).all()),
        "hybrid pending values",
    )
    if meta["route"] == "nemotron":
        state._check(meta["packet_cursor_n"] % 32000 == 0, "hybrid packet grid")
    state._check(
        meta["consumed_n"] == meta["packet_cursor_n"] + len(pending),
        "hybrid consumed cursor",
    )
    frames = arrays["hybrid_channel_frames"]
    state._check(frames.shape == (8,) and frames.dtype == np.int64, "active counters")
    state._check(bool(np.all(frames >= 0)), "negative active counters")
    state._check(
        bool(np.all(frames <= meta["native_complete_frame"])), "active counter bound"
    )
    state._check(
        meta["used_eighth_channel"] == bool(frames[7]), "eighth-channel counter"
    )
    probabilities = arrays["hybrid_posteriors"]
    state._check(
        probabilities.ndim == 2 and probabilities.shape[1] == 8, "posterior shape"
    )
    state._check(probabilities.dtype == np.float32, "posterior precision")
    state._check(bool(np.isfinite(probabilities).all()), "posterior values")
    state._check(
        bool(np.all((probabilities >= 0) & (probabilities <= 1))), "posterior range"
    )
    state._check(
        meta["posterior_first_frame"] + len(probabilities)
        == meta["native_complete_frame"],
        "posterior cursor",
    )
    overlap = arrays["hybrid_overlap"]
    state._check(overlap.ndim == 2 and overlap.shape[1] == 8, "unit overlap shape")
    state._check(len(overlap) <= len(arrays["unit_label"]), "unit overlap index")
    state._check(
        overlap.dtype == np.float64 and bool(np.isfinite(overlap).all()),
        "unit overlap values",
    )
    state._check(bool(np.all(overlap >= 0)), "negative unit overlap")


def _validate_source(meta: dict[str, Any], arrays: dict[str, np.ndarray]) -> None:
    state._check(
        meta["native_complete_frame"] * 160 <= meta["packet_cursor_n"],
        "native frame bound",
    )
    state._check(
        meta["overlap_until_s"] <= meta["native_complete_frame"] / 100,
        "overlap frame bound",
    )
    state._check(
        meta["source_sample_offset"] <= meta["consumed_n"], "source sample offset"
    )
    if meta["route"] != "nemotron":
        return
    state._check(meta["switch_emitted_at_n"] is None, "native route after switch")
    if meta["source_tail_sha256"]:
        tail = np.concatenate((arrays["audio"], arrays["hybrid_pending"]))
        count = min(meta["consumed_n"], 32000)
        tail = tail[-count:] if count else np.empty(0, np.float32)
        digest = hashlib.sha256(tail.tobytes()).hexdigest()
        state._check(digest == meta["source_tail_sha256"], "source tail digest")


def _validate_translation(meta: dict[str, Any], arrays: dict[str, np.ndarray]) -> None:
    pairs = meta["translation"]
    state._check(isinstance(pairs, list), "translation table")
    held = len(arrays["active"]) - 1
    previous = arrays["hybrid_display_labels"]
    state._check(
        previous.dtype == np.int64 and previous.ndim == 1,
        "display label history",
    )
    if meta["route"] == "clustering":
        state._check(
            np.array_equal(previous, arrays["unit_label"]),
            "display label continuity",
        )
    else:
        state._check(len(previous) == 0, "native display label history")
    seen: set[int] = set()
    for pair in pairs:
        state._check(
            isinstance(pair, (list, tuple)) and len(pair) == 2, "translation entry"
        )
        key, value = pair
        state._check(
            type(key) is int and 0 <= key < held and key not in seen,
            "translation label",
        )
        maximum = (
            meta["next_display_id"]
            if meta["switch_emitted_at_n"] is not None
            else max(meta["next_display_id"], key + 1)
        )
        state._check(type(value) is int and 0 <= value < maximum, "display identity")
        seen.add(key)
    if meta["route"] == "clustering":
        labels = set(arrays["unit_label"].tolist()) - {held}
        state._check(labels.issubset(seen), "missing translated identity")


def _decode_native(
    meta: dict[str, Any],
    arrays: dict[str, np.ndarray],
) -> tuple[Any | None, bool]:
    if meta["route"] == "clustering":
        return None, False
    try:
        state._check(meta["speaker_engine"] == "auto", "native classic route")
        state._check(
            bool(meta["asset_fingerprint"] and meta["runtime_fingerprint"]),
            "native fingerprints",
        )
        stream = native_state.restore_stream(meta["native"], arrays)
        state._check(
            stream.consumed_n == meta["packet_cursor_n"], "native packet cursor"
        )
        state._check(
            stream.next_frame == meta["native_complete_frame"],
            "native projection cursor",
        )
        return stream, False
    except (
        NemotronStateError,
        ValueError,
        KeyError,
        TypeError,
        ImportError,
        RuntimeError,
        MemoryError,
    ):
        return None, True


def _assemble(
    shadow: Any, meta: dict[str, Any], arrays: dict[str, np.ndarray], stream: Any
) -> HybridLiveState:
    from .hybrid import HybridLiveState

    engine = HybridLiveState(
        shadow,
        stream,
        speaker_engine=meta["speaker_engine"],
        reason=meta["reason"],
        fingerprints=(meta["asset_fingerprint"], meta["runtime_fingerprint"]),
    )
    for name in (
        *_INTS,
        *_STRINGS,
        "source_sequence",
        "used_eighth_channel",
        "switch_emitted_at_n",
        "overlap_until_s",
        "capture_generation",
    ):
        setattr(engine, name, meta[name])
    for name in _ARRAYS:
        setattr(engine, name, arrays[f"hybrid_{name}"].copy())
    engine.translation = dict(meta["translation"])
    return engine


def _legacy(
    live_dir: Path,
    config: LiveLabelConfig,
    meta: dict[str, Any],
    arrays: dict[str, np.ndarray],
    recording_id: int,
) -> HybridLiveState:
    from .hybrid import HybridLiveState

    shadow = state._restore(live_dir, config, meta, arrays)
    engine = HybridLiveState(
        shadow, None, speaker_engine="classic", reason="state_legacy"
    )
    engine.recording_id = recording_id
    engine.capture_generation = str(uuid4())
    engine.consumed_n = shadow.audio_end_n
    engine.packet_cursor_n = shadow.audio_end_n
    labels = set(shadow.units.label) - {shadow.held_slot}
    engine.translation = {label: label for label in labels}
    engine.display_labels = np.asarray(shadow.units.label, np.int64)
    engine.next_display_id = max(8, max(labels, default=-1) + 1)
    engine.overlap = np.zeros((shadow.units.count, 8), np.float64)
    return engine
