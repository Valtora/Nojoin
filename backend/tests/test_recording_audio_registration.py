"""Constant-time chunk registration matches the full rebuild (issue #417).

Every live segment used to rebuild the whole recording's chunk offsets and
window manifests. Registration now places only what a segment unblocks and
rebuilds only the windows that can change. These tests hold it to the full
rebuild: after every step the placed chunks and the windows must be exactly
what ``build_audio_window_specs`` gives the same chunks, and at the end the
database must match a full sync of the same files.
"""

from __future__ import annotations

import random
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from backend.tests.sqlite_schemas import (
    RECORDING_AUDIO_CHUNK_CURSORS_SCHEMA,
    RECORDING_AUDIO_CHUNKS_SCHEMA,
    RECORDINGS_SCHEMA,
)
from backend.tests.test_recording_segment_transcode import (
    RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA,
)
from backend.utils import recording_audio_registration
from backend.utils.audio_windows import build_audio_window_specs
from backend.utils.config_manager import config_manager
from backend.utils.recording_audio_registration import (
    TAIL_SCAN_BATCH_SIZE,
    ChunkStream,
    advance_recording_audio_cursor,
    register_recording_audio_chunk,
    reopen_recording_audio_cursor,
    seal_recording_audio_cursor,
)
from backend.utils.recording_audio_sync import (
    TRANSCODE_FAILED_SUFFIX,
    sync_recording_audio_chunks_from_entries,
    sync_recording_audio_window_manifests,
)

RECORDING_ID = 7
SOURCE_KIND = "browser"
# Small and coprime-ish, so groups produce several windows and partial tails.
WINDOW_MS = 4_000
HOP_MS = 1_500
SAMPLE_RATE = 16_000


@pytest.fixture(autouse=True)
def window_geometry(monkeypatch: pytest.MonkeyPatch) -> None:
    real_get = config_manager.get
    overrides = {
        "rolling_diarization_window_ms": WINDOW_MS,
        "rolling_diarization_hop_ms": HOP_MS,
    }

    def _get(key, default=None):
        if key in overrides:
            return overrides[key]
        return real_get(key, default)

    monkeypatch.setattr(config_manager, "get", _get)


def _make_engine(path: Path):
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text(RECORDINGS_SCHEMA))
        connection.execute(text(RECORDING_AUDIO_CHUNKS_SCHEMA))
        connection.execute(text(RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA))
        connection.execute(text(RECORDING_AUDIO_CHUNK_CURSORS_SCHEMA))
    return engine


@pytest.fixture
def engine(tmp_path: Path):
    engine = _make_engine(tmp_path / "incremental.sqlite")
    yield engine
    engine.dispose()


@pytest.fixture
def upload_dir(tmp_path: Path) -> Path:
    path = tmp_path / "upload"
    path.mkdir()
    return path


def _write_wav(path: Path, duration_ms: int) -> None:
    frames = SAMPLE_RATE * duration_ms // 1000
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(b"\x01\x00" * frames * 2)


def _stream(upload_dir: Path) -> ChunkStream:
    return ChunkStream(RECORDING_ID, SOURCE_KIND, upload_dir)


def _register(engine, upload_dir: Path, sequence: int, duration_ms: int):
    path = upload_dir / f"{sequence}.wav"
    _write_wav(path, duration_ms)
    with Session(engine) as session:
        row = register_recording_audio_chunk(
            session, _stream(upload_dir), sequence=sequence, storage_path=path
        )
        session.commit()
    return row


def _fail(engine, upload_dir: Path, sequence: int) -> None:
    (upload_dir / f"{sequence}{TRANSCODE_FAILED_SUFFIX}").write_text("boom\n")
    with Session(engine) as session:
        advance_recording_audio_cursor(session, _stream(upload_dir))
        session.commit()


def _manifests(engine) -> list[tuple]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                text(
                    "SELECT window_index, window_start_ms, window_end_ms,"
                    " chunk_start_sequence, chunk_end_sequence, is_partial,"
                    " is_sealed FROM recording_audio_window_manifests"
                    " ORDER BY window_index"
                )
            )
        ]


def _chunk_offsets(engine, sequences: list[int]) -> list[tuple[int, int, int]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT sequence_no, absolute_start_ms, absolute_end_ms"
                " FROM recording_audio_chunks ORDER BY sequence_no"
            )
        ).all()
    return [tuple(row) for row in rows if row[0] in sequences]


@dataclass
class _Chunk:
    sequence_no: int
    absolute_start_ms: int
    absolute_end_ms: int
    duration_ms: int
    source_kind: str = SOURCE_KIND


def _expected_placement(durations: dict[int, int], failed: set[int]) -> list[_Chunk]:
    """The chunks a full rebuild places, given what has arrived so far."""
    placed: list[_Chunk] = []
    end_ms = 0
    sequence = 0
    last = max(durations, default=-1)
    while sequence <= last:
        if sequence in durations:
            duration = durations[sequence]
            placed.append(_Chunk(sequence, end_ms, end_ms + duration, duration))
            end_ms += duration
        elif sequence not in failed:
            break
        sequence += 1
    return placed


def _spec_rows(chunks: list[_Chunk]) -> list[tuple]:
    return [
        (
            spec.window_index,
            spec.window_start_ms,
            spec.window_end_ms,
            spec.chunk_start_sequence,
            spec.chunk_end_sequence,
            int(spec.is_partial),
            int(spec.is_sealed),
        )
        for spec in build_audio_window_specs(
            chunks, target_window_ms=WINDOW_MS, hop_ms=HOP_MS, seal_tail=False
        )
    ]


def _arrival_order(rng: random.Random, count: int) -> list[int]:
    """Mostly in order, with neighbours swapped as concurrent tasks finish."""
    order = list(range(count))
    for index in range(count - 1):
        if rng.random() < 0.25:
            order[index], order[index + 1] = order[index + 1], order[index]
    return order


@pytest.mark.parametrize("seed", range(12))
def test_every_step_matches_the_full_rebuild(
    engine, upload_dir: Path, tmp_path: Path, seed: int
) -> None:
    rng = random.Random(seed)
    count = 48
    durations = {
        sequence: rng.choice([0, 500, 1_000, 1_980, 2_000, 2_500])
        for sequence in range(count)
    }
    failed = set(rng.sample(range(count), rng.randint(0, 3)))
    arrived: dict[int, int] = {}
    seen_failed: set[int] = set()

    for sequence in _arrival_order(rng, count):
        if sequence in failed:
            _fail(engine, upload_dir, sequence)
            seen_failed.add(sequence)
        else:
            _register(engine, upload_dir, sequence, durations[sequence])
            arrived[sequence] = durations[sequence]

        placed = _expected_placement(arrived, seen_failed)
        assert _manifests(engine) == _spec_rows(placed), f"after {sequence}"
        assert _chunk_offsets(engine, [c.sequence_no for c in placed]) == [
            (c.sequence_no, c.absolute_start_ms, c.absolute_end_ms) for c in placed
        ]

    reference = _make_engine(tmp_path / "reference.sqlite")
    with Session(reference) as session:
        sync_recording_audio_chunks_from_entries(
            session,
            recording_id=RECORDING_ID,
            source_kind=SOURCE_KIND,
            disk_entries=[
                (sequence, upload_dir / f"{sequence}.wav")
                for sequence in sorted(arrived)
            ],
        )
        sync_recording_audio_window_manifests(
            session,
            recording_id=RECORDING_ID,
            source_kind=SOURCE_KIND,
            seal_tail=False,
        )
        session.commit()
    assert _manifests(engine) == _manifests(reference)
    assert _chunk_offsets(engine, list(arrived)) == _chunk_offsets(
        reference, list(arrived)
    )
    reference.dispose()


def test_a_segment_still_in_flight_holds_later_chunks_back(
    engine, upload_dir: Path
) -> None:
    _register(engine, upload_dir, 0, 2_000)
    _register(engine, upload_dir, 2, 2_000)

    assert _chunk_offsets(engine, [0]) == [(0, 0, 2_000)]
    assert [row[4] for row in _manifests(engine)] == [0]

    _register(engine, upload_dir, 1, 2_000)

    assert _chunk_offsets(engine, [0, 1, 2]) == [
        (0, 0, 2_000),
        (1, 2_000, 4_000),
        (2, 4_000, 6_000),
    ]


def test_work_per_segment_does_not_grow_with_the_recording(
    engine, upload_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression itself: a late segment loads no more rows than an early one."""
    written: list[int] = []
    real_write = recording_audio_registration.write_window_manifests

    def _counting_write(session, payloads):
        written.append(len(payloads))
        real_write(session, payloads)

    monkeypatch.setattr(
        recording_audio_registration, "write_window_manifests", _counting_write
    )

    loaded_per_call: list[int] = []
    for sequence in range(400):
        path = upload_dir / f"{sequence}.wav"
        _write_wav(path, 2_000)
        loaded = 0
        with Session(engine) as session:

            def _count(_session, _instance) -> None:
                nonlocal loaded
                loaded += 1

            event.listen(session, "loaded_as_persistent", _count)
            register_recording_audio_chunk(
                session, _stream(upload_dir), sequence=sequence, storage_path=path
            )
            session.commit()
        loaded_per_call.append(loaded)

    assert max(loaded_per_call[300:]) <= max(loaded_per_call[20:60])
    # One tail-scan batch of chunks, the cursor and the few windows it touches.
    assert max(loaded_per_call[300:]) <= TAIL_SCAN_BATCH_SIZE + 8
    assert max(written) <= 3


def test_a_recording_without_a_cursor_builds_one_from_its_chunks(
    engine, upload_dir: Path
) -> None:
    """A capture in flight across the upgrade has chunk rows but no cursor."""
    for sequence in range(6):
        _register(engine, upload_dir, sequence, 2_000)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM recording_audio_chunk_cursors"))
        connection.execute(text("DELETE FROM recording_audio_window_manifests"))

    _register(engine, upload_dir, 6, 2_000)

    placed = _expected_placement({sequence: 2_000 for sequence in range(7)}, set())
    assert _manifests(engine) == _spec_rows(placed)


def test_a_rewritten_chunk_behind_the_cursor_moves_every_later_offset(
    engine, upload_dir: Path
) -> None:
    for sequence in range(4):
        _register(engine, upload_dir, sequence, 2_000)

    _register(engine, upload_dir, 1, 1_000)

    assert _chunk_offsets(engine, [0, 1, 2, 3]) == [
        (0, 0, 2_000),
        (1, 2_000, 3_000),
        (2, 3_000, 5_000),
        (3, 5_000, 7_000),
    ]
    durations = {0: 2_000, 1: 1_000, 2: 2_000, 3: 2_000}
    assert _manifests(engine) == _spec_rows(_expected_placement(durations, set()))


def test_a_sealed_recording_ignores_late_segments(engine, upload_dir: Path) -> None:
    """A transcode task that runs after finalize must not unseal its windows."""
    for sequence in range(3):
        _register(engine, upload_dir, sequence, 2_000)
    with Session(engine) as session:
        sync_recording_audio_window_manifests(
            session,
            recording_id=RECORDING_ID,
            source_kind=SOURCE_KIND,
            seal_tail=True,
        )
        seal_recording_audio_cursor(
            session, recording_id=RECORDING_ID, source_kind=SOURCE_KIND
        )
        session.commit()
    sealed = _manifests(engine)
    assert sealed and all(row[6] == 1 for row in sealed)

    assert _register(engine, upload_dir, 3, 2_000) is None
    assert _manifests(engine) == sealed


def test_reopening_a_sealed_recording_lets_it_register_again(
    engine, upload_dir: Path
) -> None:
    for sequence in range(2):
        _register(engine, upload_dir, sequence, 2_000)
    with Session(engine) as session:
        seal_recording_audio_cursor(
            session, recording_id=RECORDING_ID, source_kind=SOURCE_KIND
        )
        session.commit()
    with Session(engine) as session:
        reopen_recording_audio_cursor(
            session, recording_id=RECORDING_ID, source_kind=SOURCE_KIND
        )
        session.commit()

    assert _register(engine, upload_dir, 2, 2_000) is not None
    placed = _expected_placement({0: 2_000, 1: 2_000, 2: 2_000}, set())
    assert _manifests(engine) == _spec_rows(placed)
