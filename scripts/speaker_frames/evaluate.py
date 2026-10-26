"""The detector's gate: per-frame accuracy on synthetic meetings.

A frame is right when the detector's lit tiles and the drawn ones match one
for one (IoU of at least 0.5), or when both are empty. The training and
development seeds tune the detector; the held-out seeds are only ever
scored, and are the gate's. Run it as

    python -m speaker_frames.evaluate --set held-out --meetings 30

from ``scripts/``, or with ``--long-edge`` and ``--resize`` to see what the
sampler's resolution and resize filter cost.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from . import synth
from .detect import Box, detect_meeting, iou

GATE = 0.99
# Training and development seeds tune the detector. The held-out seeds are
# only ever scored, and are the gate's. The development set was the held-out
# set until 26 Oct 2026, when tuning had seen it and a fresh one replaced it.
SEEDS = {"training": 1_000, "development": 500_000, "held-out": 2_600_000}


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
            width, height = synth.SCREENS[i % len(synth.SCREENS)]
            scale = synth.SCALES[(i // len(synth.SCREENS)) % len(synth.SCALES)]
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
    def __init__(self, frames: Sequence[synth.SyntheticFrame]) -> None:
        self.images = [synth.decode(f.jpeg) for f in frames]

    def __len__(self) -> int:
        return len(self.images)

    def __getitem__(self, index: int) -> Any:
        return self.images[index]


def score_meeting(spec: synth.MeetingSpec) -> list[FrameScore]:
    renderer = synth.MeetingRenderer(spec)
    frames = list(renderer.frames())
    result = detect_meeting(_Decoded(frames), spec.platform)
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
    for found in parallel(score_meeting, specs(which, meetings, **overrides), workers):
        scores.extend(found)
    return summarise(scores)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", choices=tuple(SEEDS), default="training")
    parser.add_argument("--meetings", type=int, default=30, help="per platform")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--long-edge", type=int, default=synth.SAMPLER_LONG_EDGE)
    parser.add_argument("--quality", type=int, default=synth.SAMPLER_JPEG_QUALITY)
    parser.add_argument("--resize", choices=("area", "point"), default="area")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    report = run(
        args.set,
        args.meetings,
        args.workers,
        frames=args.frames,
        long_edge=args.long_edge,
        quality=args.quality,
        resize=args.resize,
    )
    print(json.dumps(report, indent=1))
    return 0 if report["overall"]["all"]["accuracy"] >= GATE else 1


if __name__ == "__main__":
    raise SystemExit(main())
