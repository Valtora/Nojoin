// @vitest-environment node

import { describe, expect, it } from "vitest";

import type { Capture } from "@/lib/api";
import type { PendingCapture } from "@/lib/captures/captureOutbox";

import { captureFileName, captureItems, capturesAmongLines } from "./captureItems";

const capture = (id: number, captureMs: number): Capture =>
  ({
    id,
    recording_id: "rec-1",
    kind: "capture",
    capture_ms: captureMs,
    use_in_notes: false,
    title: `Capture at ${captureMs}`,
    file_type: "image/png",
    created_at: "2026-10-24T09:00:00",
  }) as Capture;

const pending = (key: string, captureMs: number): PendingCapture => ({
  key,
  recordingId: "rec-1",
  captureMs,
  preview: null,
  status: "sending",
  error: null,
});

const LINES = [
  { start_ms: 2_000, text: "Let us begin." },
  { start_ms: 10_000, text: "" },
  { start_ms: 30_000, text: "Forty thousand." },
];

describe("where captures show among the lines", () => {
  it("lists saved and unsent captures together by time", () => {
    const items = captureItems([capture(1, 40_000)], [pending("p", 5_000)]);

    expect(items.map((item) => item.key)).toEqual(["p", "capture-1"]);
    expect(items[0].capture).toBeNull();
    expect(items[1].pending).toBeNull();
  });

  it("puts each after the line in progress when it was taken, as a note", () => {
    const items = captureItems(
      [capture(1, 1_000), capture(2, 12_000), capture(3, 31_000)],
      [],
    );

    const among = capturesAmongLines(LINES, items, [0, 1, 2]);

    // Before the first line it opens the transcript; a blank line is no anchor.
    expect(among.opening.map((item) => item.key)).toEqual(["capture-1"]);
    expect(among.afterLine.get(0)?.map((item) => item.key)).toEqual(["capture-2"]);
    expect(among.afterLine.get(2)?.map((item) => item.key)).toEqual(["capture-3"]);
  });

  it("shows one after a hidden line after the shown line before it", () => {
    const among = capturesAmongLines(LINES, captureItems([capture(3, 31_000)], []), [0]);

    expect(among.afterLine.get(0)?.map((item) => item.key)).toEqual(["capture-3"]);
  });

  it("names a download without a colon", () => {
    expect(captureFileName({ ...capture(1, 0), title: "Capture at 1:02:05" })).toBe(
      "capture-1-02-05.png",
    );
  });
});
