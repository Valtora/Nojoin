"""Save and load the live label engine between runs of the live task.

The engine goes to one ``.npz`` written atomically (a temporary file, synced,
then a rename), and the closed turn units to an append-only bank beside it, so
a run rewrites only the small state and not every embedding of the meeting. The
state records how many bank rows it accounts for. Rows written after that (a
task that died between the two writes) are cut off on load. The bank is synced
before the state is written, and the directory after the rename, so the state
on disk never counts a bank row that is not.

Only what is not zero is saved: the centroids of founded slots and the running
sums of open units. The rest of the state is the carried 5 s of audio and a few
numbers per unit and piece, about 0.35 MB in all.

A state written by another version or configuration is not loaded: its slots
and units mean something else. ``LiveLabelStateError`` reports that, and any
file that cannot be read or does not fit together, to the caller.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

from .engine import LiveLabelConfig, LiveLabelEngine, Piece
from .files import BANK_FILENAME, STATE_FILENAME, STATE_TMP_FILENAME
from .recluster import UnitBank

STATE_VERSION = 2

_OPEN_FIELDS = (
    "open_uid",
    "open_first",
    "open_last",
    "open_steps",
    "open_long",
)
_OPEN_SUMS = ("open_sum_long", "open_sum_all")
_UNIT_FIELDS = {
    "label": np.int32,
    "t0": np.float64,
    "stable": np.int32,
    "locked": np.bool_,
    "steps": np.int32,
    "long": np.int8,
    "row": np.int32,
}
_PIECE_FIELDS = ("piece_start", "piece_end", "piece_unit", "piece_open")
_META_INTS = ("audio_end_n", "next_end_n", "settled_ms", "bank_rows")
# Everything a damaged file, or one that does not fit together, can raise.
_UNREADABLE = (
    OSError,
    EOFError,
    ValueError,
    KeyError,
    IndexError,
    TypeError,
    zipfile.BadZipFile,
)


class LiveLabelStateError(RuntimeError):
    """The saved state cannot be used for this recording."""


def new_engine(live_dir: Path, config: LiveLabelConfig) -> LiveLabelEngine:
    """An empty engine, discarding any bank left without a state."""
    bank_path = live_dir / BANK_FILENAME
    bank_path.unlink(missing_ok=True)
    return LiveLabelEngine(config, UnitBank(bank_path, config.embedding_dim))


def save_engine(live_dir: Path, engine: LiveLabelEngine) -> None:
    units = engine.units
    clustering = engine.clustering
    meta = {
        "version": STATE_VERSION,
        "config": engine.config.fingerprint(),
        "origin_n": engine.origin_n,
        "audio_end_n": engine.audio_end_n,
        "next_end_n": engine.next_end_n,
        "next_recluster_s": engine.next_recluster_s,
        "settled_ms": engine.settled_ms,
        "bank_rows": engine.bank.rows,
        "initialised": clustering.initialised,
    }
    open_slots = np.where(units.open_uid >= 0)[0]
    arrays: dict[str, Any] = {
        "meta": np.array(json.dumps(meta)),
        "audio": engine.audio,
        "active": clustering.active,
        "piece_start": np.array([p.start for p in engine.pieces], np.float64),
        "piece_end": np.array([p.end for p in engine.pieces], np.float64),
        "piece_unit": np.array([p.unit for p in engine.pieces], np.int64),
        "piece_open": np.array([p.open for p in engine.pieces], np.bool_),
        "open_slots": open_slots,
    }
    if clustering.centers is not None:
        # A slot that is not founded has a zero centroid: reset_centers zeroes
        # every slot and add_center founds one, so only founded rows are kept.
        arrays["centers"] = clustering.centers[clustering.active]
    for name in _OPEN_FIELDS:
        arrays[name] = getattr(units, name)
    for name in _OPEN_SUMS:
        arrays[name] = getattr(units, name)[open_slots]
    for name, dtype in _UNIT_FIELDS.items():
        arrays[f"unit_{name}"] = np.array(getattr(units, name), dtype)

    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    # The bank rows first: a state on disk never counts rows that are not.
    _sync(live_dir / BANK_FILENAME)
    path = live_dir / STATE_FILENAME
    tmp = live_dir / STATE_TMP_FILENAME
    with open(tmp, "wb") as handle:
        handle.write(buffer.getvalue())
        handle.flush()
        # On disk before the rename, so a power loss leaves the old state or
        # the new one, never an empty file under the state's name.
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    # The rename itself, so a run after a power loss finds the new state.
    _sync(live_dir)


def _sync(path: Path) -> None:
    """fsync a file or directory, if it exists."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_engine(live_dir: Path, config: LiveLabelConfig) -> LiveLabelEngine | None:
    """The saved engine, or None when this recording has no state yet."""
    path = live_dir / STATE_FILENAME
    if not path.exists():
        return None
    try:
        with np.load(path) as saved:
            data = {key: saved[key] for key in saved.files}
        meta = json.loads(str(data["meta"]))
        if not isinstance(meta, dict):
            raise ValueError("state metadata is not an object")
        if meta.get("version") != STATE_VERSION or meta.get("config") != (
            config.fingerprint()
        ):
            raise LiveLabelStateError("Live label state from another version")
        return _restore(live_dir, config, meta, data)
    except _UNREADABLE as exc:
        raise LiveLabelStateError(f"Unreadable live label state: {exc!r}") from exc


def _check(condition: bool, what: str) -> None:
    if not condition:
        raise ValueError(f"live label state: {what}")


def _validate(
    live_dir: Path,
    config: LiveLabelConfig,
    meta: dict[str, Any],
    data: dict[str, np.ndarray],
) -> None:
    """Raise ValueError unless every part of the state fits together."""
    slots = config.clustering.max_speakers
    dim = config.embedding_dim
    for name in _META_INTS:
        _check(isinstance(meta[name], int) and meta[name] >= 0, name)
    _check(meta["origin_n"] is None or isinstance(meta["origin_n"], int), "origin")
    bank_rows = meta["bank_rows"]
    bank_path = live_dir / BANK_FILENAME
    bank_bytes = bank_path.stat().st_size if bank_path.exists() else 0
    _check(bank_bytes >= bank_rows * dim * 4, "bank shorter than the state")

    active = data["active"].astype(bool)
    _check(active.shape == (slots,) and not active[slots - 1], "active slots")
    _check(data["audio"].shape == (config.window_n,), "audio")
    if meta["initialised"]:
        _check(data["centers"].shape == (int(active.sum()), dim), "centers")
    open_slots = data["open_slots"]
    for name in _OPEN_FIELDS:
        _check(data[name].shape == (slots,), name)
    _check(np.array_equal(open_slots, np.where(data["open_uid"] >= 0)[0]), "open slots")
    for name in _OPEN_SUMS:
        _check(data[name].shape == (len(open_slots), dim), name)
    count = len(data["unit_label"])
    for name, dtype in _UNIT_FIELDS.items():
        field = data[f"unit_{name}"]
        _check(field.shape == (count,) and field.dtype == dtype, name)
    labels = data["unit_label"]
    _check(bool(np.all((labels >= 0) & (labels < slots))), "unit labels")
    _check(bool(np.all(data["unit_stable"] >= 0)), "unit passes")
    rows = data["unit_row"]
    _check(bool(np.all((rows >= -1) & (rows < bank_rows))), "unit bank rows")
    _check(bool(np.all(data["open_uid"] < count)), "open units")
    pieces = len(data["piece_start"])
    for name in _PIECE_FIELDS:
        _check(data[name].shape == (pieces,), name)
    _check(
        bool(np.all((data["piece_unit"] >= 0) & (data["piece_unit"] < count))),
        "piece units",
    )


def _restore(
    live_dir: Path,
    config: LiveLabelConfig,
    meta: dict[str, Any],
    data: dict[str, np.ndarray],
) -> LiveLabelEngine:
    """Rebuild the engine from a state that ``_validate`` accepted."""
    _validate(live_dir, config, meta, data)
    slots = config.clustering.max_speakers
    dim = config.embedding_dim
    open_slots = data["open_slots"]
    active = data["active"].astype(bool)
    engine = LiveLabelEngine(
        config, UnitBank(live_dir / BANK_FILENAME, dim, meta["bank_rows"])
    )
    engine.origin_n = meta["origin_n"]
    engine.audio_end_n = meta["audio_end_n"]
    engine.next_end_n = meta["next_end_n"]
    engine.next_recluster_s = float(meta["next_recluster_s"])
    engine.settled_ms = meta["settled_ms"]
    engine.audio = data["audio"].astype(np.float32)
    engine.clustering.active = active
    if meta["initialised"]:
        centers = np.zeros((slots, dim))
        centers[active] = data["centers"]
        engine.clustering.centers = centers
    units = engine.units
    for name in _OPEN_FIELDS:
        getattr(units, name)[:] = data[name]
    for name in _OPEN_SUMS:
        getattr(units, name)[open_slots] = data[name]
    for name in _UNIT_FIELDS:
        setattr(units, name, data[f"unit_{name}"].tolist())
    engine.pieces = [
        Piece(start=float(start), end=float(end), unit=int(unit), open=bool(is_open))
        for start, end, unit, is_open in zip(
            data["piece_start"],
            data["piece_end"],
            data["piece_unit"],
            data["piece_open"],
        )
    ]
    return engine
