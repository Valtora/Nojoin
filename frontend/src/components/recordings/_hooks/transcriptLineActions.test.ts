import { describe, expect, it, vi } from "vitest";
import { transcriptLineActions } from "./transcriptLineActions";
import type { TranscriptSegment } from "@/types";

const text = vi.fn();
const speaker = vi.fn();
const split = vi.fn();
vi.mock("@/lib/api", () => ({
  updateTranscriptUtteranceText: (...args: unknown[]) => text(...args),
  updateTranscriptUtteranceSpeaker: (...args: unknown[]) => speaker(...args),
}));
vi.mock("@/lib/api/transcriptSplit", () => ({
  splitTranscriptUtterance: (...args: unknown[]) => split(...args),
}));
const line: TranscriptSegment = { id: "u1", revision: 7, text: "Words", start: 0, end: 1, speaker: "UNKNOWN" };

describe("shared recording line actions", () => {
  it("sends every command against the shown source revision", async () => {
    const actions = transcriptLineActions(vi.fn());
    expect(await actions.editTranscriptLine("r1", line, "Correction")).toBe(true);
    const assignment = { name: "Alex", scope: "utterance_only" as const };
    expect(await actions.assignTranscriptLine("r1", line, assignment)).toBe(true);
    expect(await actions.splitTranscriptLine("r1", line, "w2")).toBe(true);
    expect(text).toHaveBeenCalledWith("r1", "u1", "Correction", 7);
    expect(speaker).toHaveBeenCalledWith("r1", "u1", assignment, 7);
    expect(split).toHaveBeenCalledWith("r1", "u1", "w2", 7);
  });

  it("reports a structured conflict and tells the editor to keep its text", async () => {
    text.mockRejectedValueOnce({ response: { status: 409, data: { detail: {
      code: "line_stale", message: "This line has changed. Select it again.",
    } } } });
    const notify = vi.fn();
    expect(await transcriptLineActions(notify).editTranscriptLine("r1", line, "Keep my correction")).toBe(false);
    expect(notify).toHaveBeenCalledWith("This line has changed. Select it again.");
  });
});
