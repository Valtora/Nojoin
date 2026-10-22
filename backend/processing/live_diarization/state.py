"""Save and load the live label engine between runs of the live task.

The engine goes to one ``.npz`` written atomically (a temporary file, then a
rename), and the closed turn units to an append-only bank beside it, so a run
rewrites only the small state and not every embedding of the meeting. The state
records how many bank rows it accounts for. Rows written after that (a task
that died between the two writes) are cut off on load.

A state written by another version or configuration is not loaded: its slots
and units mean something else. ``LiveLabelStateError`` reports that, and an
unreadable file, to the caller.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from .engine import LiveLabelConfig, LiveLabelEngine, Piece
from .files import BANK_FILENAME, STATE_FILENAME
from .recluster import UnitBank

STATE_VERSION = 1

_OPEN_FIELDS = (
    "open_uid",
    "open_first",
    "open_last",
    "open_steps",
    "open_long",
    "open_sum_long",
    "open_sum_all",
)
_UNIT_FIELDS = {
    "label": np.int32,
    "t0": np.float64,
    "stable": np.int32,
    "locked": np.bool_,
    "steps": np.int32,
    "long": np.int8,
    "row": np.int32,
}


class LiveLabelStateError(RuntimeError):
    """The saved state cannot be used for this recording."""


def new_engine(live_dir: Path, config: LiveLabelConfig) -> LiveLabelEngine:
    """An empty engine, discarding any bank left without a state."""
    bank_path = live_dir / BANK_FILENAME
    bank_path.unlink(missing_ok=True)
    return LiveLabelEngine(config, UnitBank(bank_path, config.embedding_dim))


def save_engine(live_dir: Path, engine: LiveLabelEngine) -> None:
    units = engine.units
    meta = {
        "version": STATE_VERSION,
        "config": engine.config.fingerprint(),
        "origin_n": engine.origin_n,
        "audio_end_n": engine.audio_end_n,
        "next_end_n": engine.next_end_n,
        "next_recluster_s": engine.next_recluster_s,
        "settled_ms": engine.settled_ms,
        "bank_rows": engine.bank.rows,
        "initialised": engine.clustering.initialised,
    }
    arrays: dict[str, Any] = {
        "meta": np.array(json.dumps(meta)),
        "audio": engine.audio,
        "active": engine.clustering.active,
        "piece_start": np.array([p.start for p in engine.pieces], np.float64),
        "piece_end": np.array([p.end for p in engine.pieces], np.float64),
        "piece_unit": np.array([p.unit for p in engine.pieces], np.int64),
        "piece_open": np.array([p.open for p in engine.pieces], np.bool_),
    }
    if engine.clustering.centers is not None:
        arrays["centers"] = engine.clustering.centers
    for name in _OPEN_FIELDS:
        arrays[name] = getattr(units, name)
    for name, dtype in _UNIT_FIELDS.items():
        arrays[f"unit_{name}"] = np.array(getattr(units, name), dtype)

    buffer = io.BytesIO()
    np.savez(buffer, **arrays)
    path = live_dir / STATE_FILENAME
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(buffer.getvalue())
    os.replace(tmp, path)


def load_engine(live_dir: Path, config: LiveLabelConfig) -> LiveLabelEngine | None:
    """The saved engine, or None when this recording has no state yet."""
    path = live_dir / STATE_FILENAME
    if not path.exists():
        return None
    try:
        with np.load(path) as saved:
            data = {key: saved[key] for key in saved.files}
        meta = json.loads(str(data["meta"]))
    except (OSError, ValueError, KeyError) as exc:
        raise LiveLabelStateError(f"Unreadable live label state: {exc}") from exc
    if meta.get("version") != STATE_VERSION or meta.get("config") != (
        config.fingerprint()
    ):
        raise LiveLabelStateError("Live label state from another version")

    bank = UnitBank(live_dir / BANK_FILENAME, config.embedding_dim, meta["bank_rows"])
    engine = LiveLabelEngine(config, bank)
    engine.origin_n = meta["origin_n"]
    engine.audio_end_n = meta["audio_end_n"]
    engine.next_end_n = meta["next_end_n"]
    engine.next_recluster_s = meta["next_recluster_s"]
    engine.settled_ms = meta["settled_ms"]
    engine.audio = data["audio"]
    engine.clustering.active = data["active"].astype(bool)
    if meta["initialised"]:
        engine.clustering.centers = data["centers"]
    for name in _OPEN_FIELDS:
        setattr(engine.units, name, data[name])
    for name in _UNIT_FIELDS:
        setattr(engine.units, name, data[f"unit_{name}"].tolist())
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
