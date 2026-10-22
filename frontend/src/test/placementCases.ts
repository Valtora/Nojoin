import { readFileSync } from "node:fs";
import path from "node:path";

import type { PlacementNote, PlacementSegment } from "@/lib/notes/placement";

/**
 * The golden placement cases that `backend/tests/test_timeline_note_placement.py`
 * runs against `place_timeline_notes`, for the frontend's placement and the
 * views built on it.
 */
export interface PlacementCase {
  name: string;
  segments: (PlacementSegment & { end_ms?: number; end?: number; text: string })[];
  notes: (PlacementNote & { quoted_text?: string })[];
  /** Every note in timeline order; `after_index` for body notes only. */
  expected: { id: string; slot: "head" | "body" | "tail"; after_index?: number }[];
}

const CASES_PATH = path.resolve(
  __dirname,
  "../../../backend/tests/fixtures/timeline_note_placement_cases.json",
);

export const PLACEMENT_CASES: PlacementCase[] = JSON.parse(
  readFileSync(CASES_PATH, "utf8"),
).cases;

/**
 * What a view showing every line renders for a case, in order: `line:i` for
 * the line at index i and `note:id` for a body note, head and tail notes left
 * out (they have the notes panel).
 */
export const expectedRenderOrder = (golden: PlacementCase): string[] => {
  const bodies = golden.expected.filter((item) => item.slot === "body");
  const order: string[] = [];
  const notesAfter = (index: number) =>
    bodies
      .filter((item) => item.after_index === index)
      .forEach((item) => order.push(`note:${item.id}`));
  notesAfter(-1);
  golden.segments.forEach((_, index) => {
    order.push(`line:${index}`);
    notesAfter(index);
  });
  return order;
};
