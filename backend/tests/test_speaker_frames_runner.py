"""Screen study (#653): the analysis writes summary.json and nothing else.

A small study is drawn at runtime: synthetic Teams meetings stored as the
sampler stores frames, and a timeline as the export writes it. The runner
reads them in a separate interpreter that records every write and every
attempt to reach out; the only write allowed is summary.json, and the
summary may hold numbers, ids and result words only.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.speaker_frames import guard, run, synth

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
PREREG = SCRIPTS / "speaker_frames" / "PREREG.md"

# Runs the runner with an audit hook of its own, separate from the runner's
# guard, that lists every write and every way out and prints them at exit.
RECORDER = """
import json, os, sys
writes, outward = [], []
def hook(event, args):
    if event == "open" and len(args) >= 3:
        path, mode, flags = args[:3]
        wants = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))
        if wants:
            writes.append(os.fsdecode(path) if not isinstance(path, int) else str(path))
    elif event.startswith(("socket.", "subprocess.", "os.exec", "os.posix_spawn", "os.system")):
        outward.append(event)
sys.addaudithook(hook)
sys.path.insert(0, sys.argv[1])
from speaker_frames import run
try:
    code = run.main(sys.argv[2:])
finally:
    sys.stderr.write("RECORDED " + json.dumps({"writes": writes, "outward": outward}) + "\\n")
sys.exit(code)
"""


def _study(root: Path, meetings: int = 2) -> tuple[Path, Path, list[str]]:
    """Synthetic Teams meetings as the sampler stores them, and their timeline."""
    frames_root = root / "frames"
    frames_root.mkdir()
    timeline = root / "timeline.jsonl"
    ids = []
    rows = []
    for order in range(1, meetings + 1):
        public_id = str(uuid4())
        ids.append(public_id)
        spec = synth.MeetingSpec(
            platform="teams",
            seed=900 + order,
            frames=24,
            screen=synth.Screen(1920, 1080, 1.5),
            participants=4,
            kinds=("gallery",),
        )
        renderer = synth.MeetingRenderer(spec)
        folder = frames_root / public_id
        folder.mkdir()
        index = []
        for k, frame in enumerate(renderer.frames()):
            sequence, offset = k // 10, (k % 10) * 1000
            (folder / f"{sequence}-{offset}.jpg").write_bytes(frame.jpeg)
            index.append(
                {
                    "sequence": sequence,
                    "offset_ms": offset,
                    "received_at": "2026-10-26T00:00:00Z",
                    "bytes": len(frame.jpeg),
                    "sha256": hashlib.sha256(frame.jpeg).hexdigest(),
                    "width": 1280,
                    "height": 720,
                    "platform_hint": None,
                    "tick": k,
                    "lost_before": 0,
                    "frame_ts_us": k * 1_000_000,
                    "grab_delay_ms": 5,
                }
            )
        (folder / "index.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in index)
        )
        utterances = [
            [k * 1000, k * 1000 + 1000, p, p, 100 + p, 0]
            for k, speaking in enumerate(renderer.speech)
            for p in sorted(speaking)[:1]
        ]
        rows.append(
            {
                "recording": public_id,
                "platform": "teams",
                "platform_order": order,
                "chunks": [[s, s * 10_000] for s in range(3)],
                "utterances": utterances,
            }
        )
    timeline.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return frames_root, timeline, ids


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def _run(
    scripts: Path, args: list[str]
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[str]]]:
    result = subprocess.run(
        [sys.executable, "-B", "-c", RECORDER, str(scripts), *args],
        capture_output=True,
        text=True,
        timeout=900,
    )
    line = next(x for x in result.stderr.splitlines() if x.startswith("RECORDED "))
    return result, json.loads(line.removeprefix("RECORDED "))


@pytest.fixture(scope="module")
def study(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path, Path, list[str]]:
    root = tmp_path_factory.mktemp("study")
    frames, timeline, ids = _study(root)
    return root, frames, timeline, ids


def test_the_runner_writes_summary_json_and_nothing_else(
    study: tuple[Path, Path, Path, list[str]],
) -> None:
    root, frames, timeline, ids = study
    out = root / "out"
    out.mkdir()
    before = _snapshot(root)
    sha = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    result, recorded = _run(
        SCRIPTS,
        [
            "--frames",
            str(frames),
            "--timeline",
            str(timeline),
            "--prereg",
            str(PREREG),
            "--prereg-sha256",
            sha,
            "--out",
            str(out),
            "--gate-meetings",
            "1",
            "--workers",
            "2",
        ],
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert [p.name for p in out.iterdir()] == ["summary.json"]
    assert recorded["writes"] == [str(out / "summary.json")]
    assert recorded["outward"] == []
    after = _snapshot(root)
    assert set(after) - set(before) == {"out/summary.json"}
    assert all(after[k] == v for k, v in before.items())

    raw = (out / "summary.json").read_bytes()
    assert b"\xff\xd8" not in raw and b"PNG" not in raw
    summary = json.loads(raw)
    guard.check_summary(summary, ids)
    assert summary["prereg_sha256"] == sha
    assert summary["synthetic_gate"]["result"] in ("pass", "fail")


def test_a_prereg_that_does_not_match_stops_before_anything(
    study: tuple[Path, Path, Path, list[str]],
) -> None:
    root, frames, timeline, _ = study
    out = root / "out-mismatch"
    out.mkdir()
    result, recorded = _run(
        SCRIPTS,
        [
            "--frames",
            str(frames),
            "--timeline",
            str(timeline),
            "--prereg",
            str(PREREG),
            "--prereg-sha256",
            "0" * 64,
            "--out",
            str(out),
        ],
    )
    assert result.returncode != 0
    assert recorded["writes"] == []
    assert list(out.iterdir()) == []


def test_the_analysis_scores_a_synthetic_study(
    study: tuple[Path, Path, Path, list[str]],
) -> None:
    _, frames, timeline, ids = study
    recordings = run.load_timeline(timeline)
    result = run.analyse(recordings, frames, workers=1)
    guard.check_summary(result, ids)
    calibration = result["calibration"]["teams"]
    assert calibration["recordings"] == [ids[0]]
    assert calibration["agreement"] > 0.5
    held_out = result["meetings"][ids[1]]
    assert held_out["role"] == "held_out"
    assert held_out["frames"] == 24
    assert held_out["visible_speech_share"] > 0.5
    assert result["rules"]["precondition"]["result"] == "pass"


def test_the_guard_refuses_any_other_write(tmp_path: Path) -> None:
    allowed = tmp_path / "summary.json"
    attempt = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from speaker_frames import guard; from pathlib import Path;"
        "guard.install(Path(sys.argv[2]));"
        "open(sys.argv[3], 'wb').write(b'\\xff\\xd8')"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            attempt,
            str(SCRIPTS),
            str(allowed),
            str(tmp_path / "crop.jpg"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "write refused" in result.stderr
    assert not (tmp_path / "crop.jpg").exists()


@pytest.mark.parametrize(
    "event",
    [
        "import socket; socket.create_connection(('127.0.0.1', 9))",
        "import subprocess; subprocess.run(['true'])",
        "import os; os.mkdir(sys.argv[3])",
    ],
)
def test_the_guard_refuses_ways_out(tmp_path: Path, event: str) -> None:
    attempt = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from speaker_frames import guard; from pathlib import Path;"
        f"guard.install(Path(sys.argv[2])); {event}"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            attempt,
            str(SCRIPTS),
            str(tmp_path / "summary.json"),
            str(tmp_path / "d"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "refused" in result.stderr


@pytest.mark.parametrize(
    "summary",
    [
        {"speaker": "Alice Example"},
        {"rules": {"note": "the budget is confidential"}},
        {"frame": "\xff\xd8\xff\xe0"},
        {"Alice": 1},
        {"share": float("nan")},
        {"crop": b"\xff\xd8"},
    ],
)
def test_a_summary_with_anything_but_numbers_and_ids_is_refused(
    summary: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        guard.check_summary(summary, [str(uuid4())])


def test_a_summary_with_numbers_ids_and_results_is_accepted() -> None:
    recording = str(uuid4())
    guard.check_summary(
        {
            "meetings": {
                recording: {
                    "platform": "zoom",
                    "share": 0.5,
                    "tiles": 3,
                    "role": "held_out",
                }
            },
            "rules": {"matching": {"result": "inconclusive", "value": None}},
            "code_sha256": "a" * 64,
        },
        [recording],
    )


def test_the_summary_is_written_once(tmp_path: Path) -> None:
    recording = str(uuid4())
    guard.write_summary(tmp_path, {"value": 1}, [recording])
    with pytest.raises(FileExistsError):
        guard.write_summary(tmp_path, {"value": 2}, [recording])
    with pytest.raises(ValueError):
        guard.write_summary(tmp_path / "x", {}, ["not-an-id"])
