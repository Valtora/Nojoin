import { describe, expect, it } from "vitest";

import { PLACEMENT_CASES } from "@/test/placementCases";

import { moveTargets, targetBeside } from "./noteMoves";
import {
  bodyNotesAmongLines,
  placeTimelineNotes,
  segmentStartMs,
  type PlacementNote,
  type PlacementSegment,
} from "./placement";

const MOVED: PlacementNote = {
  id: "moved",
  kind: "point",
  anchor_ms: 0,
  created_at: "2026-01-01T10:00:00Z",
};

/**
 * Where a view showing `visibleIndexes` shows a point note anchored at
 * `anchorMs`: the shown line it follows, or -1 before every line. Placement
 * itself decides, as it does for every reader.
 */
const shownAfter = (
  segments: readonly PlacementSegment[],
  visibleIndexes: readonly number[],
  anchorMs: number,
  others: readonly PlacementNote[] = [],
) => {
  const among = bodyNotesAmongLines(
    placeTimelineNotes(segments, [...others, { ...MOVED, anchor_ms: anchorMs }]),
    visibleIndexes,
  );
  if (among.opening.some((note) => note.id === MOVED.id)) return -1;
  for (const [line, notes] of among.afterLine) {
    if (notes.some((note) => note.id === MOVED.id)) return line;
  }
  throw new Error("the moved note is not shown");
};

/** A small seeded generator (mulberry32), so a failing case can be run again. */
const random = (seed: number) => () => {
  seed = (seed + 0x6d2b79f5) | 0;
  let mixed = Math.imul(seed ^ (seed >>> 15), seed | 1);
  mixed ^= mixed + Math.imul(mixed ^ (mixed >>> 7), mixed | 61);
  return ((mixed ^ (mixed >>> 14)) >>> 0) / 4_294_967_296;
};

/**
 * A transcript as the finished view can get one: mostly in time order, with
 * lines that start together, a few listed out of order, blank ones, and some
 * hidden (an unknown speaker's).
 */
const randomTranscript = (seed: number) => {
  const next = random(seed);
  const count = 1 + Math.floor(next() * 14);
  let start = Math.floor(next() * 3) * 1000;
  const segments: PlacementSegment[] = [];
  for (let index = 0; index < count; index += 1) {
    const roll = next();
    if (roll < 0.15 && index > 0) {
      // Starts with the line before it.
    } else if (roll < 0.25 && index > 0) {
      start = Math.max(0, start - 1 - Math.floor(next() * 4000));
    } else {
      start += 1 + Math.floor(next() * 6000);
    }
    segments.push({ start_ms: start, end_ms: start + 2500, text: next() < 0.1 ? "  " : `Line ${index}.` });
  }
  const visibleIndexes = segments.map((_, index) => index).filter(() => next() > 0.2);
  return { segments, visibleIndexes };
};

const goldenTranscripts = PLACEMENT_CASES.filter((golden) => golden.segments.length > 0).map(
  (golden) => [golden.name, golden.segments] as const,
);

describe("moveTargets", () => {
  it.each(goldenTranscripts)("round-trips on the golden lines of “%s”", (_name, segments) => {
    const every = segments.map((_, index) => index);
    const targets = moveTargets(segments, every);
    expect(targets.length).toBeGreaterThan(0);
    for (const target of targets) {
      expect(target.anchorMs).toBe(segmentStartMs(segments[target.afterIndex]));
      expect(shownAfter(segments, every, target.anchorMs)).toBe(target.afterIndex);
    }
  });

  it("round-trips: a note dropped after a shown line shows after that line", () => {
    let checked = 0;
    for (let seed = 1; seed <= 400; seed += 1) {
      const { segments, visibleIndexes } = randomTranscript(seed);
      // Notes already in place never move the dropped one.
      const others = [{ ...MOVED, id: "other", anchor_ms: segmentStartMs(segments[0]) + 1 }];
      for (const target of moveTargets(segments, visibleIndexes)) {
        expect(visibleIndexes).toContain(target.afterIndex);
        expect(target.anchorMs).toBe(segmentStartMs(segments[target.afterIndex]));
        expect(shownAfter(segments, visibleIndexes, target.anchorMs, others), `seed ${seed}`).toBe(
          target.afterIndex,
        );
        checked += 1;
      }
    }
    expect(checked).toBeGreaterThan(1000);
  });

  it("offers every line of an ordinary transcript, in time order with nothing hidden", () => {
    for (let seed = 1; seed <= 200; seed += 1) {
      const next = random(seed);
      let start = 0;
      const segments = Array.from({ length: 1 + Math.floor(next() * 12) }, (_, index) => {
        start += 1 + Math.floor(next() * 5000);
        return { start_ms: start, end_ms: start + 900, text: `Line ${index}.` };
      });
      const every = segments.map((_, index) => index);
      expect(moveTargets(segments, every).map((target) => target.afterIndex)).toEqual(every);
    }
  });

  it("leaves out a gap a later line would take: a shared start, a blank line", () => {
    const segments = [
      { start_ms: 0, text: "Opening." },
      { start_ms: 4000, text: "Alex speaks." },
      { start_ms: 4000, text: "Blair speaks over Alex." },
      { start_ms: 9000, text: " " },
      { start_ms: 12000, text: "Closing." },
    ];
    expect(moveTargets(segments, [0, 1, 2, 3, 4])).toEqual([
      { afterIndex: 0, anchorMs: 0 },
      { afterIndex: 2, anchorMs: 4000 },
      { afterIndex: 4, anchorMs: 12000 },
    ]);
  });

  it("anchors at a hidden line's shown neighbour, so the note shows where it was dropped", () => {
    const segments = [
      { start_ms: 0, text: "Opening." },
      { start_ms: 5000, text: "Unknown speaker." },
      { start_ms: 8000, text: "Budget." },
    ];
    // Line 1 is hidden: a drop after line 0 anchors at line 0's start.
    expect(moveTargets(segments, [0, 2])).toEqual([
      { afterIndex: 0, anchorMs: 0 },
      { afterIndex: 2, anchorMs: 8000 },
    ]);
    // Hidden and starting with line 0, it takes the note, which still shows after line 0.
    const together = [segments[0], { start_ms: 0, text: "Unknown speaker." }, segments[2]];
    expect(moveTargets(together, [0, 2])).toEqual([
      { afterIndex: 0, anchorMs: 0 },
      { afterIndex: 2, anchorMs: 8000 },
    ]);
  });
});

describe("targetBeside", () => {
  const targets = [
    { afterIndex: 0, anchorMs: 0 },
    { afterIndex: 2, anchorMs: 4000 },
    { afterIndex: 4, anchorMs: 12000 },
  ];

  it("steps to the nearest gap above or below the note's own", () => {
    expect(targetBeside(targets, 2, 1)).toEqual({ afterIndex: 4, anchorMs: 12000 });
    expect(targetBeside(targets, 2, -1)).toEqual({ afterIndex: 0, anchorMs: 0 });
    // From a gap that is not offered itself, the next one either side.
    expect(targetBeside(targets, 3, -1)).toEqual({ afterIndex: 2, anchorMs: 4000 });
    expect(targetBeside(targets, 1, 1)).toEqual({ afterIndex: 2, anchorMs: 4000 });
  });

  it("has nowhere to go past either end, and steps down from before every line", () => {
    expect(targetBeside(targets, 0, -1)).toBeNull();
    expect(targetBeside(targets, 4, 1)).toBeNull();
    expect(targetBeside(targets, -1, -1)).toBeNull();
    expect(targetBeside(targets, -1, 1)).toEqual({ afterIndex: 0, anchorMs: 0 });
  });
});
