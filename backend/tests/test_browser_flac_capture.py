# ruff: noqa: F811 -- the API fixtures are imported from test_recording_segment_transcode and requested by name
"""Browser capture uploads FLAC segments and finalize writes a FLAC master.

The browser cuts sample-exact 2 s segments and uploads them as FLAC, which the
API decodes in process into the canonical WAV the live lane reads (issue #417).
These tests cover that path end to end through the API, the formats it refuses,
and the finalize side: an exact master, a sealed cursor, and coverage measured
against the browser's own clock.
"""

from __future__ import annotations

import io
import wave
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
import soundfile
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.processing.live_diarization.files import LIVE_LABEL_FILENAMES
from backend.tests.test_live_speaker_labels_lifecycle import write_label_state
from backend.tests.test_recording_segment_transcode import (  # noqa: F401 -- fixtures
    TRUSTED_ORIGIN,
    _chunk_rows_for_recording,
    _lookup_internal_recording_id,
    _recording_temp_dir,
    api_app,
    client,
    live_dispatches,
    make_wav_bytes,
    pipeline_metrics,
    set_session_cookie,
    sqlite_urls,
    sync_engine,
    test_session_maker,
    transcode_dispatches,
)
from backend.utils.browser_capture_audio import (
    BrowserAudioFormatError,
    decode_browser_flac_segment,
    write_browser_master_audio,
)
from backend.utils.time import utc_now

SEGMENT_FRAMES = 32_000


def _pcm(frames: int, channels: int = 2, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(-3000, 3000, size=(frames, channels), dtype=np.int16)


def make_flac_bytes(
    frames: int = SEGMENT_FRAMES,
    *,
    channels: int = 2,
    sample_rate: int = 16_000,
    seed: int = 0,
) -> bytes:
    buffer = io.BytesIO()
    soundfile.write(
        buffer,
        _pcm(frames, channels, seed),
        sample_rate,
        format="FLAC",
        subtype="PCM_16",
    )
    return buffer.getvalue()


def _read_wav(path: Path) -> tuple[int, int, int, np.ndarray]:
    with wave.open(str(path), "rb") as wav_file:
        data = np.frombuffer(
            wav_file.readframes(wav_file.getnframes()), dtype="<i2"
        ).reshape(-1, wav_file.getnchannels())
        return (
            wav_file.getframerate(),
            wav_file.getnchannels(),
            wav_file.getsampwidth(),
            data,
        )


async def _start(client: AsyncClient, session_maker: sessionmaker) -> tuple[str, int]:
    set_session_cookie(client)
    response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "FLAC meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert response.status_code == 200
    public_id = response.json()["id"]
    return public_id, await _lookup_internal_recording_id(
        session_maker, public_id=public_id
    )


async def _upload(
    client: AsyncClient, public_id: str, sequence: int, body: bytes
) -> int:
    response = await client.post(
        f"/api/v1/recordings/{public_id}/segment",
        params={"sequence": sequence},
        headers={"Origin": TRUSTED_ORIGIN},
        files={"file": (f"{sequence}.flac", body, "audio/flac")},
    )
    return response.status_code


@pytest.fixture
def captured_metrics(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    from backend.api.v1.endpoints import recordings as recordings_module

    metrics: list[dict] = []
    monkeypatch.setattr(
        recordings_module,
        "record_pipeline_metric",
        lambda *, stage, recording_id, payload, log: metrics.append(
            {"stage": stage, "recording_id": recording_id, "payload": payload}
        ),
    )
    return metrics


GOLDEN_FIXTURE = Path(__file__).parent / "fixtures" / "browser_capture_golden.flac"


def _golden_pcm(frames: int = 5_000) -> np.ndarray:
    """The generator in frontend/src/lib/capture/pcm/flacEncoder.test.ts."""
    pcm = np.zeros((frames, 2), dtype=np.int16)
    state = 1
    for index in range(frames):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        noise = ((state >> 16) % 201) - 100
        saw = ((index * 29) % 4000) - 2000
        pcm[index, 0] = 0 if index < 1_200 else saw + noise
        pcm[index, 1] = ((index // 50) % 2) * 6000 - 3000 + noise // 4
    return pcm


def test_libflac_decodes_what_the_browser_encoder_writes() -> None:
    """The browser's FLAC encoder is ours, so libFLAC is the referee.

    The frontend test pins the encoder to this fixture byte for byte; this one
    proves the fixture is a stream libFLAC accepts, CRCs and all, and that it
    holds exactly the samples that went in.
    """
    info = soundfile.info(str(GOLDEN_FIXTURE))
    decoded, sample_rate = soundfile.read(
        str(GOLDEN_FIXTURE), dtype="int16", always_2d=True
    )

    assert (info.format, info.subtype, info.channels) == ("FLAC", "PCM_16", 2)
    assert sample_rate == 16_000
    assert np.array_equal(decoded, _golden_pcm())


def test_decoding_a_segment_writes_the_canonical_wav(tmp_path: Path) -> None:
    source = tmp_path / "0.flac"
    source.write_bytes(make_flac_bytes(seed=3))
    destination = tmp_path / "0.wav"

    assert decode_browser_flac_segment(source, destination) == SEGMENT_FRAMES

    sample_rate, channels, width, data = _read_wav(destination)
    assert (sample_rate, channels, width) == (16_000, 2, 2)
    assert np.array_equal(data, _pcm(SEGMENT_FRAMES, seed=3))
    assert sorted(path.name for path in tmp_path.iterdir()) == ["0.flac", "0.wav"]


@pytest.mark.parametrize(
    "body",
    [
        make_flac_bytes(channels=1),
        make_flac_bytes(sample_rate=48_000),
        make_flac_bytes()[: len(make_flac_bytes()) // 2],
        b"fLaC" + b"\x00" * 64,
    ],
    ids=["mono", "48khz", "truncated", "garbage"],
)
def test_a_segment_in_any_other_shape_is_refused(tmp_path: Path, body: bytes) -> None:
    source = tmp_path / "0.flac"
    source.write_bytes(body)

    with pytest.raises(BrowserAudioFormatError):
        decode_browser_flac_segment(source, tmp_path / "0.wav")
    assert not (tmp_path / "0.wav").exists()


def test_the_master_is_every_frame_in_order(tmp_path: Path) -> None:
    paths = []
    for sequence, frames in enumerate([SEGMENT_FRAMES, SEGMENT_FRAMES, 7_123]):
        path = tmp_path / f"{sequence}.wav"
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(2)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16_000)
            wav_file.writeframes(_pcm(frames, seed=sequence).tobytes())
        paths.append(path)

    seconds = write_browser_master_audio(paths, tmp_path / "master.flac")

    master, sample_rate = soundfile.read(tmp_path / "master.flac", dtype="int16")
    expected = np.concatenate(
        [
            _pcm(frames, seed=index)
            for index, frames in enumerate([32_000, 32_000, 7_123])
        ]
    )
    assert sample_rate == 16_000
    assert np.array_equal(master, expected)
    assert seconds == pytest.approx(71_123 / 16_000)


def test_the_master_refuses_segments_of_different_formats(tmp_path: Path) -> None:
    first = tmp_path / "0.wav"
    first.write_bytes(make_wav_bytes(channels=2))
    second = tmp_path / "1.wav"
    second.write_bytes(make_wav_bytes(channels=1))

    with pytest.raises(BrowserAudioFormatError):
        write_browser_master_audio([first, second], tmp_path / "master.flac")
    assert not (tmp_path / "master.flac").exists()


@pytest.mark.anyio
async def test_a_flac_upload_reaches_the_live_lane_without_a_transcode(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    live_dispatches,
    transcode_dispatches,
    tmp_path: Path,
) -> None:
    public_id, recording_id = await _start(client, test_session_maker)

    assert await _upload(client, public_id, 0, make_flac_bytes()) == 200

    upload_dir = _recording_temp_dir(tmp_path, recording_id, create=False)
    assert transcode_dispatches == []
    assert live_dispatches == [(recording_id, 0)]
    assert not (upload_dir / "0.flac").exists()
    assert np.array_equal(_read_wav(upload_dir / "0.wav")[3], _pcm(SEGMENT_FRAMES))
    assert await _chunk_rows_for_recording(
        test_session_maker, recording_id=recording_id
    ) == [(0, str(upload_dir / "0.wav"))]


@pytest.mark.anyio
async def test_a_corrupt_segment_costs_one_gap_and_holds_nothing_back(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    live_dispatches,
    captured_metrics,
    tmp_path: Path,
) -> None:
    public_id, recording_id = await _start(client, test_session_maker)
    corrupt = make_flac_bytes(seed=1)[:-200]

    assert await _upload(client, public_id, 0, make_flac_bytes()) == 200
    assert await _upload(client, public_id, 1, corrupt) == 200
    assert await _upload(client, public_id, 2, make_flac_bytes(seed=2)) == 200

    upload_dir = _recording_temp_dir(tmp_path, recording_id, create=False)
    assert (upload_dir / "1.corrupt").read_bytes() == corrupt
    assert live_dispatches == [(recording_id, 0), (recording_id, 2)]
    assert [
        m["payload"]["sequence"]
        for m in captured_metrics
        if m["stage"] == "segment_corrupt_skipped"
    ] == [1]
    async with test_session_maker() as session:
        offsets = (
            await session.execute(
                text(
                    "SELECT sequence_no, absolute_start_ms FROM recording_audio_chunks"
                    " WHERE recording_id = :recording_id ORDER BY sequence_no"
                ),
                {"recording_id": recording_id},
            )
        ).all()
    # Sequence 2 was placed straight after 0: the dead segment did not block it.
    assert [tuple(row) for row in offsets] == [(0, 0), (2, 2_000)]

    finalize = await client.post(
        f"/api/v1/recordings/{public_id}/finalize",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert finalize.status_code == 200
    assert finalize.json()["duration_seconds"] == pytest.approx(4.0)


@pytest.mark.anyio
async def test_uploads_write_last_activity_at_most_once_per_interval(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    public_id, recording_id = await _start(client, test_session_maker)

    async def _last_activity():
        async with test_session_maker() as session:
            return (
                await session.execute(
                    text("SELECT last_activity_at FROM recordings WHERE id = :id"),
                    {"id": recording_id},
                )
            ).scalar_one()

    started = await _last_activity()
    assert await _upload(client, public_id, 0, make_flac_bytes()) == 200
    assert await _last_activity() == started

    stale = utc_now() - timedelta(minutes=5)
    async with test_session_maker() as session:
        await session.execute(
            text("UPDATE recordings SET last_activity_at = :stale WHERE id = :id"),
            {"stale": stale, "id": recording_id},
        )
        await session.commit()
    assert await _upload(client, public_id, 1, make_flac_bytes(seed=1)) == 200
    assert str(await _last_activity()) > str(stale)


@pytest.mark.anyio
async def test_finalize_writes_an_exact_master_and_judges_coverage_by_the_browser(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    captured_metrics,
    tmp_path: Path,
) -> None:
    public_id, recording_id = await _start(client, test_session_maker)
    for sequence in range(3):
        body = make_flac_bytes(seed=sequence)
        assert await _upload(client, public_id, sequence, body) == 200
    assert await _upload(client, public_id, 3, make_flac_bytes(8_000, seed=3)) == 200

    finalize = await client.post(
        f"/api/v1/recordings/{public_id}/finalize",
        params={"client_elapsed_seconds": 6.5},
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert finalize.status_code == 200
    assert finalize.json()["duration_seconds"] == pytest.approx(6.5)
    async with test_session_maker() as session:
        audio_path = (
            await session.execute(
                text("SELECT audio_path FROM recordings WHERE id = :id"),
                {"id": recording_id},
            )
        ).scalar_one()
    master, _ = soundfile.read(audio_path, dtype="int16")
    assert audio_path.endswith(".flac")
    assert np.array_equal(
        master,
        np.concatenate(
            [_pcm(SEGMENT_FRAMES, seed=s) for s in range(3)] + [_pcm(8_000, seed=3)]
        ),
    )
    coverage = next(m for m in captured_metrics if m["stage"] == "capture_coverage")
    assert coverage["payload"]["client_elapsed_seconds"] == 6.5
    assert coverage["payload"]["coverage_ratio"] == 1.0


@pytest.mark.anyio
async def test_a_transcode_that_runs_after_finalize_leaves_the_sealed_windows(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    live_dispatches,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The race that unsealed every window of a 2 h meeting after its stop."""
    from backend.processing import segment_transcode as segment_transcode_module

    monkeypatch.setattr(
        segment_transcode_module,
        "_run_ffmpeg_transcode",
        lambda input_path, output_path: output_path.write_bytes(
            make_wav_bytes(channels=2)
        ),
    )
    public_id, recording_id = await _start(client, test_session_maker)
    upload = await client.post(
        f"/api/v1/recordings/{public_id}/segment",
        params={"sequence": 0},
        headers={"Origin": TRUSTED_ORIGIN},
        files={"file": ("0.webm", b"webm-opus-segment", "audio/webm")},
    )
    assert upload.status_code == 200
    finalize = await client.post(
        f"/api/v1/recordings/{public_id}/finalize",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert finalize.status_code == 200

    async def _sealed() -> list[int]:
        async with test_session_maker() as session:
            return [
                int(row[0])
                for row in (
                    await session.execute(
                        text(
                            "SELECT is_sealed FROM recording_audio_window_manifests"
                            " WHERE recording_id = :id"
                        ),
                        {"id": recording_id},
                    )
                ).all()
            ]

    assert await _sealed() == [1]

    result = segment_transcode_module.transcode_segment_task(recording_id, 0)

    assert result == {"status": "received", "segment": 0}
    assert await _sealed() == [1]
    assert live_dispatches == []


@pytest.mark.anyio
async def test_finalize_decodes_a_flac_segment_its_upload_left_behind(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    tmp_path: Path,
) -> None:
    """An API restart between storing and decoding a segment loses nothing."""
    public_id, recording_id = await _start(client, test_session_maker)
    assert await _upload(client, public_id, 1, make_flac_bytes(seed=1)) == 200
    upload_dir = _recording_temp_dir(tmp_path, recording_id, create=False)
    (upload_dir / "0.flac").write_bytes(make_flac_bytes(seed=0))

    finalize = await client.post(
        f"/api/v1/recordings/{public_id}/finalize",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert finalize.status_code == 200
    assert finalize.json()["duration_seconds"] == pytest.approx(4.0)
    assert (upload_dir / "0.wav").exists()
    assert not (upload_dir / "0.flac").exists()


async def _capture_with_label_state(
    client: AsyncClient, session_maker: sessionmaker, tmp_path: Path
) -> tuple[str, Path, list[Path]]:
    public_id, recording_id = await _start(client, session_maker)
    for sequence in range(2):
        assert await _upload(client, public_id, sequence, make_flac_bytes()) == 200
    live_dir = _recording_temp_dir(tmp_path, recording_id, create=False) / "live"
    return public_id, live_dir, write_label_state(live_dir)


@pytest.mark.anyio
async def test_finalize_removes_the_label_state(
    client: AsyncClient, test_session_maker: sessionmaker, tmp_path: Path
) -> None:
    public_id, live_dir, paths = await _capture_with_label_state(
        client, test_session_maker, tmp_path
    )
    kept = live_dir / "state.json"
    kept.write_text("{}")

    finalize = await client.post(
        f"/api/v1/recordings/{public_id}/finalize",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert finalize.status_code == 200
    assert not any(path.exists() for path in paths)
    # Only the labels' files: the lane's own state is not this feature's.
    assert kept.exists()


@pytest.mark.anyio
async def test_a_master_that_cannot_be_built_leaves_no_label_state_behind(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.v1.endpoints import recordings as recordings_module
    from backend.utils import recording_storage

    failed_root = tmp_path / "failed"
    monkeypatch.setattr(
        recording_storage, "recordings_failed_dir", lambda create=True: failed_root
    )

    def refuse(paths, output_path):
        raise BrowserAudioFormatError("segments differ")

    monkeypatch.setattr(recordings_module, "write_browser_master_audio", refuse)
    public_id, live_dir, _ = await _capture_with_label_state(
        client, test_session_maker, tmp_path
    )
    failed_root.mkdir()

    finalize = await client.post(
        f"/api/v1/recordings/{public_id}/finalize",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert finalize.status_code == 400
    # The segments were set aside for recovery, without the label state.
    moved = list(failed_root.iterdir())
    assert len(moved) == 1
    assert any(moved[0].glob("*.wav"))
    leftovers = [p for p in moved[0].rglob("*") if p.name in LIVE_LABEL_FILENAMES]
    assert leftovers == []
    assert not live_dir.exists()
