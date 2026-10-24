// @vitest-environment node

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { TIMELINE_NOTE_ERROR_CODES } from "@/types";

// The same file pins the backend's TimelineNoteErrorCode
// (test_the_error_codes_are_the_ones_the_frontend_pins).
const PINNED = path.resolve(
  __dirname,
  "../../../../backend/tests/fixtures/timeline_note_error_codes.json",
);

describe("timeline note error codes", () => {
  it("are the ones the API sends", () => {
    const pinned: string[] = JSON.parse(readFileSync(PINNED, "utf8"));
    expect([...TIMELINE_NOTE_ERROR_CODES].sort()).toEqual([...pinned].sort());
  });
});
