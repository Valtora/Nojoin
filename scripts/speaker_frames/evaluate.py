"""The detector's gate: per-frame accuracy on synthetic meetings.

A frame is right when the detector's lit tiles and the drawn ones match one
for one (IoU of at least 0.5), or when both are empty. The training and
development seeds tune the detector; the held-out seeds are only ever
scored, and are the gate's. Run it as

    python -m speaker_frames.evaluate --set held-out

from ``scripts/``, or with ``--long-edge`` and ``--resize`` to see what the
sampler's resolution and resize filter cost. A long run splits into parts,
``--part 1/4 --save part1.json`` and so on, that ``--combine`` sums up.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import synth
from .detect import Box, detect_meeting, iou

GATE = 0.99
# Meetings per platform in each set: 30 meetings of 20 minutes, some 30,000
# frames, and every screen size and scaling on each platform.
MEETINGS = 10
# Training and development seeds tune the detector. The held-out seeds are
# only ever scored, and are the gate's. The development set was the held-out
# set until 26 Oct 2026, when tuning had seen it and a fresh one replaced it;
# that one, 2600000, gave way in turn when the meetings grew to 20 minutes.
SEEDS = {"training": 1_000, "development": 500_000, "held-out": 3_700_000}


@dataclass(frozen=True)
class FrameScore:
    platform: str
    layout: str
    screen: str
    scale: float
    ring_px: float
    correct: bool


def frame_correct(predicted: Sequence[Box], truth: Sequence[Box]) -> bool:
    if len(predicted) != len(truth):
        return False
    left = list(predicted)
    for box in truth:
        best = max(left, key=lambda p: iou(p, box), default=None)
        if best is None or iou(best, box) < 0.5:
            return False
        left.remove(best)
    return True


def layout_name(truth: synth.FrameTruth) -> str:
    if truth.kind != "gallery":
        return truth.kind
    for low, high in synth.GALLERY_BANDS:
        if low <= truth.tile_count <= high:
            return f"gallery {low}-{high}"
    return "gallery"


def specs(which: str, meetings: int, **overrides: object) -> list[synth.MeetingSpec]:
    """Meetings spread evenly over every screen size and scaling."""
    base = SEEDS[which]
    out = []
    for p, platform in enumerate(synth.PLATFORMS):
        for i in range(meetings):
            # Every screen and every scaling within the first ten meetings,
            # and every pairing of the two within thirty.
            width, height = synth.SCREENS[i % len(synth.SCREENS)]
            scale = synth.SCALES[
                (i + 2 * (i // len(synth.SCREENS))) % len(synth.SCALES)
            ]
            band = synth.GALLERY_BANDS[(i + p) % len(synth.GALLERY_BANDS)]
            seed = base + p * 10_000 + i
            out.append(
                synth.MeetingSpec(
                    platform=platform,
                    seed=seed,
                    screen=synth.Screen(width, height, scale),
                    participants=band[0] + seed % (band[1] - band[0] + 1),
                    **overrides,  # type: ignore[arg-type]
                )
            )
    return out


class _Decoded:
    """A meeting's frames, decoded when read: a 20-minute meeting's frames
    do not fit in memory all decoded at once."""

    def __init__(self, frames: Sequence[synth.SyntheticFrame]) -> None:
        self.jpegs = [f.jpeg for f in frames]
        self._last: tuple[int, Any] = (-1, None)

    def __len__(self) -> int:
        return len(self.jpegs)

    def __getitem__(self, index: int) -> Any:
        if self._last[0] != index:
            self._last = (index, synth.decode(self.jpegs[index]))
        return self._last[1]


def score_meeting(spec: synth.MeetingSpec, workers: int = 1) -> list[FrameScore]:
    """Each kept frame of one synthetic meeting, right or wrong. ``workers``
    forked processes draw the meeting and run the detector's passes."""
    renderer = synth.MeetingRenderer(spec)
    frames = list(renderer.frames(workers))
    result = detect_meeting(_Decoded(frames), spec.platform, workers=workers)
    screen = renderer.screen
    ring_px = (
        renderer.look.ring_width
        * screen.scale
        * spec.long_edge
        / max(screen.width, screen.height)
    )
    return [
        FrameScore(
            platform=spec.platform,
            layout=layout_name(frame.truth),
            screen=f"{screen.width}x{screen.height}",
            scale=screen.scale,
            ring_px=round(ring_px, 2),
            correct=frame_correct(result.lit_boxes(i), frame.truth.lit),
        )
        for i, frame in enumerate(frames)
    ]


def summarise(scores: Iterable[FrameScore]) -> dict[str, dict[str, dict[str, float]]]:
    """Accuracy overall and by platform, layout, screen, scaling and ring width."""
    groups: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for s in scores:
        groups["overall"]["all"].append(s.correct)
        groups["platform"][s.platform].append(s.correct)
        groups["layout"][s.layout].append(s.correct)
        groups["platform_layout"][f"{s.platform} {s.layout}"].append(s.correct)
        groups["screen"][s.screen].append(s.correct)
        groups["scale"][f"{s.scale:g}"].append(s.correct)
        groups["ring_px"][_ring_band(s.ring_px)].append(s.correct)
    return {
        name: {
            key: {"frames": len(v), "accuracy": sum(v) / len(v)}
            for key, v in sorted(group.items())
        }
        for name, group in groups.items()
    }


def _ring_band(px: float) -> str:
    for edge in (0.75, 1.0, 1.5, 2.0, 3.0):
        if px < edge:
            return f"<{edge:g}"
    return ">=3"


def parallel(
    function: Callable[[Any], Any], items: Sequence[Any], workers: int
) -> Iterator[Any]:
    """``map`` over forked workers. A forked worker inherits the runner's
    guard; starting a fresh interpreter would need a new program, which the
    guard refuses."""
    if workers <= 1:
        yield from map(function, items)
        return
    with multiprocessing.get_context("fork").Pool(workers) as pool:
        yield from pool.imap(function, items)


def run(
    which: str, meetings: int, workers: int = 1, **overrides: object
) -> dict[str, dict[str, dict[str, float]]]:
    scores: list[FrameScore] = []
    for spec in specs(which, meetings, **overrides):
        scores.extend(score_meeting(spec, workers))
    return summarise(scores)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", choices=tuple(SEEDS), default="training")
    parser.add_argument("--meetings", type=int, default=MEETINGS, help="per platform")
    parser.add_argument("--frames", type=int, default=synth.MEETING_TICKS)
    parser.add_argument("--long-edge", type=int, default=synth.SAMPLER_LONG_EDGE)
    parser.add_argument("--quality", type=int, default=synth.SAMPLER_JPEG_QUALITY)
    parser.add_argument("--resize", choices=("area", "point"), default="area")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--part",
        default="1/1",
        help="K/N: every Nth meeting from the Kth, to split a run",
    )
    parser.add_argument("--save", type=Path, help="also write the frame scores here")
    parser.add_argument(
        "--combine", type=Path, nargs="+", help="summarise saved parts instead"
    )
    args = parser.parse_args(argv)
    if args.combine:
        scores = [
            FrameScore(**row)
            for path in args.combine
            for row in json.loads(path.read_text())
        ]
        whole = True
    else:
        k, n = (int(v) for v in args.part.split("/"))
        chosen = specs(
            args.set,
            args.meetings,
            frames=args.frames,
            long_edge=args.long_edge,
            quality=args.quality,
            resize=args.resize,
        )[k - 1 :: n]
        scores = [
            score for spec in chosen for score in score_meeting(spec, args.workers)
        ]
        whole = n == 1
        if args.save:
            args.save.write_text(json.dumps([asdict(score) for score in scores]))
    report = summarise(scores)
    print(json.dumps(report, indent=1))
    return 0 if not whole or report["overall"]["all"]["accuracy"] >= GATE else 1


if __name__ == "__main__":
    raise SystemExit(main())
