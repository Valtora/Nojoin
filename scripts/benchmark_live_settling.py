"""Replay two-second settling publications and measure submitted JSON bytes.

Run as ``python -m scripts.benchmark_live_settling`` with the project's virtualenv
and NOJOIN_TEST_POSTGRES_URL set to a disposable
test PostgreSQL. This measures actual JSON SQL bind payloads, not TOAST/WAL sizes;
all canonical/event/state JSON writes count, and closure is reported separately.
"""

import argparse
import importlib
import json
import os
from collections import defaultdict
from typing import Any

from psycopg.types.json import Json, Jsonb
from sqlalchemy import create_engine, event, text
from sqlmodel import Session

from backend.core.db import driver_urls
from backend.models.recording import RecordingStatus
from backend.models.transcript import Transcript
from backend.tests.postgres_setup import fresh_schema
from backend.tests.storage_hygiene_harness import add_recording
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.canonical_pipeline.live_settling import (
    SettlingPass,
    close_live_settling,
    publish_live_settling,
)
from backend.utils.config_manager import config_manager
from backend.utils.transcript_settling import (
    GateChunk,
    capture_publication_token,
    load_capture_publication,
)

SCHEMA = "live_settling_write_benchmark"


class JsonWrites:
    """Count every JSON bind value sent in an INSERT or UPDATE statement."""

    def __init__(self) -> None:
        self.bytes: int = 0
        self.by_table: dict[str, int] = defaultdict(int)
        self.projection_writes: int = 0

    def before_execute(self, *args: Any) -> None:
        statement, parameters, context, many = args[2:]
        if not statement.lstrip().upper().startswith(("INSERT", "UPDATE")):
            return
        table = context.compiled.statement.table.name
        batches = parameters if many else [parameters]
        for batch in batches:
            entries = batch.items() if isinstance(batch, dict) else enumerate(batch)
            for key, value in entries:
                if isinstance(value, Json | Jsonb):
                    size = len(json.dumps(value.obj, ensure_ascii=True).encode("utf-8"))
                    self.bytes += size
                    self.by_table[table] += size
                    if table == "transcripts" and key in {"segments", "text"}:
                        self.projection_writes += 1


def replay(hours: float, *, pending_speakers: bool = False) -> dict[str, Any]:
    """Publish a constant-rate synthetic meeting through the real fenced service."""
    importlib.import_module("backend.models.registry")
    url = os.environ.get("NOJOIN_TEST_POSTGRES_URL")
    if not url:
        raise SystemExit("Set NOJOIN_TEST_POSTGRES_URL to a disposable test PostgreSQL")
    engine = create_engine(
        driver_urls(url)[1], connect_args={"options": f"-csearch_path={SCHEMA},public"}
    )
    original_get = config_manager.get
    config_manager.get = lambda key, default=None: (
        ("auto" if pending_speakers else "off")
        if key == "live_speaker_labels"
        else original_get(key, default)
    )
    try:
        fresh_schema(engine, SCHEMA)
        recording_id = add_recording(
            engine, "synthetic.wav", status=RecordingStatus.UPLOADING
        )
        with Session(engine) as session:
            session.add(
                Transcript(
                    recording_id=recording_id,
                    segments=[],
                    settling_state={
                        "version": 1,
                        "eligible": True,
                        "generation": "synthetic",
                        "capture_epoch": 0,
                        "sealed": False,
                        "phase": "live",
                        "policy": {"k": 3, "window_s": 10, "enabled": True},
                    },
                )
            )
            session.commit()
        writes = JsonWrites()
        event.listen(engine, "before_cursor_execute", writes.before_execute)
        samples: list[int] = []
        steps = int(hours * 3600 / 2)
        for step in range(steps):
            end = (step + 1) * 2000
            words = [
                {
                    "word": f"word-{index}",
                    "start_ms": index * 500 + 50,
                    "end_ms": index * 500 + 400,
                }
                for index in range(max(0, (end - 10000) // 500), end // 500)
            ]
            before = writes.bytes
            with Session(engine) as session:
                transcript, recording = load_capture_publication(
                    session, recording_id, lock=False
                )
                request = SettlingPass(
                    recording_id,
                    capture_publication_token(recording, transcript),
                    f"step-{step}",
                    step,
                    end - 10000,
                    end,
                    transcript.change_sequence,
                    metadata={
                        "source_channel_activity": {
                            "channel_count": 1,
                            "sample_count": step + 1,
                        }
                    },
                    # Retain a full window's ledger to include the upper bound
                    # of ordinary gate bookkeeping in every SQL measurement.
                    gate_chunks=[
                        GateChunk(
                            sequence=index,
                            start_ms=index * 2000,
                            end_ms=(index + 1) * 2000,
                            missed=False,
                            consumed=True,
                        )
                        for index in range(max(0, step - 4), step + 1)
                    ],
                )
                result = publish_live_settling(
                    session,
                    request=request,
                    words=words,
                    decide_speaker=(
                        lambda start, end: LiveLabelDecision("UNKNOWN", False, None)
                    )
                    if pending_speakers
                    else None,
                )
                if not result.accepted:
                    raise RuntimeError(f"Synthetic step {step} was rejected")
                session.commit()
            samples.append(writes.bytes - before)
        step_total, step_tables = writes.bytes, dict(writes.by_table)
        per_step_projection_writes = writes.projection_writes
        with Session(engine) as session:
            close_live_settling(session, recording_id=recording_id)
            session.commit()
        result = {
            "hours": hours,
            "pending_speakers": pending_speakers,
            "steps": steps,
            "words_per_second": 2,
            "gate_ledger_chunks_max": 5,
            "step_json_bytes_total": step_total,
            "step_json_bytes_min": min(samples),
            "step_json_bytes_max": max(samples),
            "step_json_bytes_mean": round(sum(samples) / len(samples), 2),
            "first_100_mean": round(sum(samples[:100]) / len(samples[:100]), 2),
            "last_100_mean": round(sum(samples[-100:]) / len(samples[-100:]), 2),
            "sample_bytes": {
                str(index + 1): samples[index]
                for index in (0, min(99, steps - 1), steps // 2, steps - 1)
            },
            "step_json_bytes_by_table": step_tables,
            "per_step_compatibility_projection_writes": per_step_projection_writes,
            "closure_json_bytes": writes.bytes - step_total,
        }
        event.remove(engine, "before_cursor_execute", writes.before_execute)
        return result
    finally:
        config_manager.get = original_get
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=2)
    parser.add_argument("--pending-speakers", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(replay(args.hours, pending_speakers=args.pending_speakers), indent=2)
    )


if __name__ == "__main__":
    main()
