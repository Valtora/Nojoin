"""Run the Screen study's analysis and write summary.json, nothing else.

Meant for the capped container with no network that ``run_study.sh``
starts, with the frames mounted read-only. In order, it:

1. installs the guard, so the only file it can write is summary.json;
2. checks PREREG.md against the SHA-256 posted on #653;
3. runs the detector's gate on the held-out synthetic meetings, and stops
   there, before reading any real frame, if it scores under 99%. The gate
   can instead run first in parts, ``--gate-part 1/6`` and so on, each a
   run of its own that writes only that part's counts; ``--gate-from``
   then reads those summaries in place of running the gate;
4. reads the timeline the export wrote and each study meeting's frames;
5. fits each platform's lag on its calibration meeting, then scores the
   held-out meetings against the pass rules.

    python -m speaker_frames.run --frames /frames --timeline /input/timeline.jsonl \\
        --prereg /analysis/speaker_frames/PREREG.md --prereg-sha256 <hex> --out /out
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, JpegImagePlugin  # noqa: F401 - loaded before the guard

from . import align, detect, evaluate, guard, metrics
from .align import Highlight, Utterance

SCHEMA = "speaker-frames-summary-1"
PLATFORMS = ("teams", "zoom", "meet")
# Tiles count as on screen within 30 s of a ring seen in the same layout.
VISIBLE_GAP_MS = 30_000
GATE_MEETINGS = evaluate.MEETINGS


@dataclass
class Recording:
    public_id: str
    platform: str
    order: int
    chunks: dict[int, int]
    utterances: list[Utterance]

    @property
    def role(self) -> str:
        return "calibration" if self.order == 1 else "held_out"


@dataclass
class Observed:
    """One meeting as the frames showed it."""

    recording: Recording
    highlight: Highlight
    numbers: dict[str, Any] = field(default_factory=dict)


def load_timeline(path: Path) -> list[Recording]:
    """The export: one JSON object per line, as export_timeline.sql writes."""
    recordings: list[Recording] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            public_id = str(row["recording"])
            if public_id in seen:
                raise ValueError("a recording carries more than one study tag")
            seen.add(public_id)
            if not guard.UUID.match(public_id) or row["platform"] not in PLATFORMS:
                raise ValueError("a timeline row has an unexpected id or platform")
            recordings.append(
                Recording(
                    public_id=public_id,
                    platform=row["platform"],
                    order=int(row["platform_order"]),
                    chunks={int(s): int(t) for s, t in row["chunks"]},
                    utterances=[_utterance(u) for u in row["utterances"]],
                )
            )
    return recordings


def _utterance(values: Sequence[Any]) -> Utterance:
    start, end, diarization, pipeline, reviewed, overlap = values
    return Utterance(
        start_ms=int(start),
        end_ms=int(end),
        diarization=None if diarization is None else int(diarization),
        pipeline=None if pipeline is None else int(pipeline),
        reviewed=None if reviewed is None else int(reviewed),
        overlap_rank=int(overlap or 0),
    )


@dataclass(frozen=True)
class FrameFile:
    path: Path
    time_ms: int
    tick: int
    lost_before: int | None


def frame_files(folder: Path, chunks: dict[int, int]) -> tuple[list[FrameFile], int]:
    """A meeting's frames in time order, and how many could not be placed."""
    index = folder / "index.jsonl"
    if not index.is_file():
        return [], 0
    rows = [
        json.loads(line)
        for line in index.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    times = align.frame_times(rows, chunks)
    files = []
    for row, time in zip(rows, times, strict=True):
        name = f"{int(row['sequence'])}-{int(row['offset_ms'])}.jpg"
        if time is None or not (folder / name).is_file():
            continue
        lost = row.get("lost_before")
        files.append(
            FrameFile(
                folder / name,
                time,
                int(row.get("tick", -1)),
                None if lost is None else int(lost),
            )
        )
    files.sort(key=lambda f: (f.time_ms, f.tick))
    return files, len(rows) - len(files)


class _Lazy:
    """Frames decoded on demand, so a three-hour meeting fits in memory."""

    def __init__(self, files: Sequence[FrameFile]) -> None:
        self.files = files

    def __len__(self) -> int:
        return len(self.files)

    def __getitem__(self, index: int) -> np.ndarray:
        with Image.open(self.files[index].path) as image:
            return np.asarray(image.convert("RGB"))


def observe(recording: Recording, frames_root: Path) -> Observed:
    files, unplaced = frame_files(frames_root / recording.public_id, recording.chunks)
    numbers: dict[str, Any] = {"frames": len(files), "frames_unplaced": unplaced}
    if not files:
        return Observed(recording, Highlight.empty(), numbers)
    result = detect.detect_meeting(_Lazy(files), recording.platform)
    numbers.update(
        slots=len(result.slots),
        layouts=len(set(result.layout_ids().values())),
        ring_hue=round(detect.hue(result.colour), 1),
        ring_calibrated=result.calibrated,
        platform_from_frames=detect.nearest_platform(result.colour),
    )
    return Observed(recording, highlight(result, files), numbers)


def highlight(result: detect.MeetingResult, files: Sequence[FrameFile]) -> Highlight:
    """The detector's frames as tiles on the transcript clock.

    A tile is a slot within a layout; a layout that comes back keeps its
    number. Tiles are on screen within ``VISIBLE_GAP_MS`` of a ring seen in
    the same layout.
    """
    layout_ids = result.layout_ids()
    times = np.array([f.time_ms for f in files], np.int64)
    lit: list[tuple[int, ...]] = []
    layout_of: dict[int, int] = {}
    for frame in result.frames:
        layout = layout_ids.get(frame.layout) if frame.layout is not None else None
        tiles = (
            tuple(sorted(_tile(layout, slot) for slot in frame.lit))
            if layout is not None
            else ()
        )
        for tile in tiles:
            layout_of[tile] = layout  # type: ignore[assignment]
        lit.append(tiles)
    visible = _visible(
        times,
        [
            layout_ids.get(f.layout) if f.layout is not None else None
            for f in result.frames
        ],
        lit,
    )
    until = align.held_until(times.tolist(), [f.lost_before for f in files])
    return Highlight(times, tuple(lit), visible, layout_of, until)


def _tile(layout: int, slot: int) -> int:
    return layout * 10_000 + slot


def _visible(
    times: np.ndarray, layouts: Sequence[int | None], lit: Sequence[tuple[int, ...]]
) -> np.ndarray:
    visible = np.zeros(len(times), bool)
    sightings: dict[int, list[int]] = {}
    for time, layout, tiles in zip(times.tolist(), layouts, lit, strict=True):
        if layout is not None and tiles:
            sightings.setdefault(layout, []).append(time)
    for k, (time, layout) in enumerate(zip(times.tolist(), layouts, strict=True)):
        seen = sightings.get(layout, []) if layout is not None else []
        if seen:
            at = int(np.searchsorted(seen, time))
            near = [seen[i] for i in (at - 1, at) if 0 <= i < len(seen)]
            visible[k] = min(abs(time - s) for s in near) <= VISIBLE_GAP_MS
    return visible


Part = tuple[int, int]


def _gate_counts(
    meetings: int, workers: int, ticks: int | None, part: Part = (1, 1)
) -> dict[str, list[int]]:
    """Frames scored and frames right, by platform, for every Nth held-out
    meeting from the Kth."""
    shorter = {} if ticks is None else {"frames": ticks}
    chosen = evaluate.specs("held-out", meetings, **shorter)[part[0] - 1 :: part[1]]
    counts = {platform: [0, 0] for platform in PLATFORMS}
    for spec in chosen:
        for score in evaluate.score_meeting(spec, workers):
            counts[score.platform][0] += 1
            counts[score.platform][1] += int(score.correct)
    return counts


def _gate_result(counts: dict[str, list[int]], parts: int) -> dict[str, Any]:
    frames = sum(c[0] for c in counts.values())
    accuracy = sum(c[1] for c in counts.values()) / frames if frames else 0.0
    return {
        "threshold": evaluate.GATE,
        "frames": frames,
        "accuracy": accuracy,
        "parts": parts,
        "by_platform": {k: c[1] / c[0] for k, c in counts.items() if c[0]},
        "result": metrics.PASS
        if frames and accuracy >= evaluate.GATE
        else metrics.FAIL,
    }


def gate(meetings: int, workers: int, ticks: int | None = None) -> dict[str, Any]:
    """The detector's held-out synthetic gate.

    ``ticks`` shortens each meeting, for tests; the gate itself runs them
    whole.
    """
    return _gate_result(_gate_counts(meetings, workers, ticks), 1)


def gate_part(
    part: Part, meetings: int, workers: int, ticks: int | None = None
) -> dict[str, Any]:
    """One part of the gate, so a long gate can run as several shorter jobs."""
    counts = _gate_counts(meetings, workers, ticks, part)
    return {
        "part": part[0],
        "parts": part[1],
        "meetings": meetings,
        "ticks": ticks,
        "frames": {k: c[0] for k, c in counts.items()},
        "correct": {k: c[1] for k, c in counts.items()},
    }


def gate_from(
    paths: Sequence[Path], summary: dict[str, Any], meetings: int, ticks: int | None
) -> dict[str, Any]:
    """The gate from its parts' summaries, each run on this code and this
    PREREG.md, every part once."""
    parts = []
    for path in paths:
        part = json.loads(path.read_text(encoding="utf-8"))
        if any(
            part.get(k) != summary[k]
            for k in ("schema", "prereg_sha256", "code_sha256")
        ):
            raise SystemExit(f"{path} was run on other code or another PREREG.md")
        parts.append(part["gate_part"])
    count = len(parts)
    if sorted(p["part"] for p in parts) != list(range(1, count + 1)) or any(
        (p["parts"], p["meetings"], p["ticks"]) != (count, meetings, ticks)
        for p in parts
    ):
        raise SystemExit("the gate's parts do not make up one whole gate")
    counts = {k: [0, 0] for k in PLATFORMS}
    for p in parts:
        for k in PLATFORMS:
            counts[k][0] += int(p["frames"][k])
            counts[k][1] += int(p["correct"][k])
    return _gate_result(counts, count)


def analyse(
    recordings: Sequence[Recording], frames_root: Path, workers: int
) -> dict[str, Any]:
    observed = list(evaluate.parallel(_Observer(frames_root), recordings, workers))
    calibration: dict[str, Any] = {}
    lags: dict[str, int] = {}
    for platform in PLATFORMS:
        mine = [
            o
            for o in observed
            if o.recording.platform == platform and o.recording.role == "calibration"
        ]
        lag, agreement = align.fit_lag(
            [(o.recording.utterances, o.highlight) for o in mine]
        )
        lags[platform] = lag
        calibration[platform] = {
            "recordings": [o.recording.public_id for o in mine],
            "lag_ms": lag,
            "agreement": agreement,
        }
    held_out = []
    meetings: dict[str, Any] = {}
    for o in observed:
        bins = align.meeting_bins(
            o.recording.utterances, o.highlight, lags[o.recording.platform]
        )
        meeting = metrics.Meeting(
            o.recording.public_id, o.recording.platform, bins, o.recording.utterances
        )
        meetings[o.recording.public_id] = {
            **o.numbers,
            **_meeting_numbers(meeting),
            "role": o.recording.role,
            "platform": o.recording.platform,
        }
        if o.recording.role == "held_out":
            held_out.append(meeting)
    return {
        "calibration": calibration,
        "meetings": meetings,
        "rules": metrics.score(held_out),
    }


class _Observer:
    def __init__(self, frames_root: Path) -> None:
        self.frames_root = frames_root

    def __call__(self, recording: Recording) -> Observed:
        return observe(recording, self.frames_root)


def _meeting_numbers(meeting: metrics.Meeting) -> dict[str, Any]:
    shown, spoken = metrics.visible_speech(meeting.bins)
    speakers, pipeline, tiles = metrics.speaker_counts(meeting.bins)
    matched, right, single = metrics.matching(meeting.bins)
    return {
        "speech_s": spoken * align.BIN_MS / 1000,
        "visible_speech_share": shown / spoken if spoken else None,
        "speakers": speakers,
        "pipeline_speakers": pipeline,
        "tiles": tiles,
        "matched_share": matched / single if single else None,
        "match_accuracy": right / matched if matched else None,
    }


def code_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--frames", type=Path)
    parser.add_argument("--timeline", type=Path)
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--prereg-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--gate-meetings", type=int, default=GATE_MEETINGS, help="per platform"
    )
    parser.add_argument(
        "--gate-ticks", type=int, default=None, help="per meeting, for tests"
    )
    parser.add_argument(
        "--gate-part", help="K/N: run only part K of the gate in N, write it and stop"
    )
    parser.add_argument(
        "--gate-from", type=Path, nargs="+", help="the parts --gate-part wrote"
    )
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    if not args.gate_part and (args.frames is None or args.timeline is None):
        parser.error("--frames and --timeline are needed unless --gate-part is given")
    if any(args.out.iterdir()):
        raise SystemExit("the output folder must be empty")
    guard.install(args.out / guard.SUMMARY_NAME)
    prereg = hashlib.sha256(args.prereg.read_bytes()).hexdigest()
    if prereg != args.prereg_sha256.lower():
        raise SystemExit("PREREG.md does not match the posted SHA-256")
    summary: dict[str, Any] = {
        "schema": SCHEMA,
        "prereg_sha256": prereg,
        "code_sha256": code_sha256(),
    }
    if args.gate_part:
        k, n = (int(v) for v in args.gate_part.split("/"))
        if not 1 <= k <= n:
            raise SystemExit("--gate-part is K/N with K from 1 to N")
        summary["gate_part"] = gate_part(
            (k, n), args.gate_meetings, args.workers, args.gate_ticks
        )
        guard.write_summary(args.out, summary, [])
        return 0
    if args.gate_from:
        summary["synthetic_gate"] = gate_from(
            args.gate_from, summary, args.gate_meetings, args.gate_ticks
        )
    else:
        summary["synthetic_gate"] = gate(
            args.gate_meetings, args.workers, args.gate_ticks
        )
    recordings = load_timeline(args.timeline)
    ids = [r.public_id for r in recordings]
    if summary["synthetic_gate"]["result"] == metrics.PASS:
        summary.update(analyse(recordings, args.frames, args.workers))
    guard.write_summary(args.out, summary, ids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
