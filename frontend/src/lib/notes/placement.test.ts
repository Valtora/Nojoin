import { describe, expect, it } from "vitest";

import { PLACEMENT_CASES } from "@/test/placementCases";

import {
  bodyNotesAmongLines,
  createdAtMicros,
  placeTimelineNotes,
  type NotePlacement,
  type PlacementNote,
} from "./placement";

const described = (placement: NotePlacement<PlacementNote>) =>
  placement.slot === "body"
    ? { id: placement.note.id, slot: placement.slot, after_index: placement.afterIndex }
    : { id: placement.note.id, slot: placement.slot };

const note = (id: string, anchorMs: number, createdAt = "2026-01-01T10:00:00Z"): PlacementNote => ({
  id,
  kind: "point",
  anchor_ms: anchorMs,
  created_at: createdAt,
});

describe("placeTimelineNotes", () => {
  it("reads the golden file", () => {
    expect(PLACEMENT_CASES.length).toBeGreaterThan(10);
  });

  it.each(PLACEMENT_CASES.map((golden) => [golden.name, golden] as const))(
    "%s",
    (_name, golden) => {
      expect(placeTimelineNotes(golden.segments, golden.notes).map(described)).toEqual(
        golden.expected,
      );
    },
  );

  it("orders notes made in the same millisecond by the API's microseconds", () => {
    const segments = [{ start_ms: 0, text: "Opening." }];
    const later = note("a", 1000, "2026-10-21T10:00:00.000900");
    const earlier = note("b", 1000, "2026-10-21T10:00:00.000100");

    expect(placeTimelineNotes(segments, [later, earlier]).map((p) => p.note.id)).toEqual([
      "b",
      "a",
    ]);
  });

  it("reads a zone offset, and a time without one as UTC", () => {
    expect(createdAtMicros("2026-10-21T12:00:00+02:00")).toBe(
      createdAtMicros("2026-10-21T10:00:00"),
    );
    expect(createdAtMicros("2026-10-21T10:00:00.5Z")).toBe(
      Date.UTC(2026, 9, 21, 10) * 1000 + 500_000,
    );
  });

  it("rounds a legacy start in seconds the way Python's round() does", () => {
    // 0.0025 s is 2.5 ms: Python rounds the half to the even 2, not up to 3.
    const segments = [
      { start: 0, text: "First." },
      { start: 0.0025, text: "Second." },
    ];
    const at2 = note("at-2", 2);

    expect(placeTimelineNotes(segments, [at2])[0].afterIndex).toBe(1);
  });
});

describe("bodyNotesAmongLines", () => {
  const segments = [
    { start_ms: 0, text: "Opening." },
    { start_ms: 5000, text: "Hidden line." },
    { start_ms: 9000, text: "Closing." },
  ];

  it("shows a note after a hidden line after the visible line before it", () => {
    const placements = placeTimelineNotes(segments, [note("n1", 6000)]);

    const among = bodyNotesAmongLines(placements, [0, 2]);

    expect(among.opening).toEqual([]);
    expect([...among.afterLine.entries()].map(([line, notes]) => [line, notes.map((n) => n.id)])).toEqual([
      [0, ["n1"]],
    ]);
  });

  it("opens the view with a note that follows only hidden lines", () => {
    const placements = placeTimelineNotes(segments, [note("early", 100), note("mid", 6000)]);

    const among = bodyNotesAmongLines(placements, [2]);

    expect(among.opening.map((n) => n.id)).toEqual(["early", "mid"]);
    expect(among.afterLine.size).toBe(0);
  });

  it("leaves head and tail notes out", () => {
    const placements = placeTimelineNotes(segments, [
      { id: "head", kind: "head", created_at: "2026-01-01T10:00:00Z" },
      { id: "tail", kind: "tail", created_at: "2026-01-01T10:00:00Z" },
    ]);

    const among = bodyNotesAmongLines(placements, [0, 1, 2]);

    expect(among.opening).toEqual([]);
    expect(among.afterLine.size).toBe(0);
  });
});

describe("lines in start order", () => {
  // The views sort lines by start (then end and id); the prompts keep the
  // stored order, which the writers emit by start. Which way equal starts are
  // ordered must not change which lines a note follows, only which side of a
  // blank line it lands, and a blank line shows nothing.
  it("puts the same lines before a note whichever way equal starts are ordered", () => {
    let seed = 7;
    const random = () => {
      seed = (seed * 1_103_515_245 + 12_345) % 2 ** 31;
      return seed / 2 ** 31;
    };
    const linesBefore = (rows: { id: string; start_ms: number; text: string }[], atMs: number) => {
      const [placement] = placeTimelineNotes(rows, [note("n", atMs)]);
      const after = placement.slot === "body" ? placement.afterIndex : -1;
      return rows
        .slice(0, after + 1)
        .filter((row) => row.text.trim() !== "")
        .map((row) => row.id)
        .sort();
    };
    for (let trial = 0; trial < 2_000; trial += 1) {
      const rows = Array.from({ length: 6 }, (_, index) => ({
        id: `r${index}`,
        start_ms: Math.floor(random() * 4) * 1_000,
        end_ms: 0,
        text: random() < 0.15 ? "  " : `line ${index}`,
      }));
      const ascending = [...rows].sort(
        (a, b) => a.start_ms - b.start_ms || a.id.localeCompare(b.id),
      );
      const descending = [...rows].sort(
        (a, b) => a.start_ms - b.start_ms || b.id.localeCompare(a.id),
      );
      const atMs = Math.floor(random() * 5_000);
      expect(linesBefore(ascending, atMs)).toEqual(linesBefore(descending, atMs));
    }
  });
});
