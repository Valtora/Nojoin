// @vitest-environment node

import { describe, expect, it } from "vitest";

import { ClientStatus, RecordingStatus } from "@/types";
import { recordingGistLabel, recordingTitleHint } from "./recordingPresentation";

describe("recording gist presentation", () => {
  it.each([undefined, null, "", "  "])("omits an unavailable gist (%s)", (gist) => {
    const recording = { gist, status: RecordingStatus.PROCESSED };
    expect(recordingGistLabel(recording)).toBeUndefined();
    expect(recordingTitleHint(recording, "Click to rename")).toBe("Click to rename");
  });

  it.each([
    RecordingStatus.UPLOADING, RecordingStatus.PAUSED, RecordingStatus.QUEUED, RecordingStatus.PROCESSING,
  ])("marks %s summaries as provisional", (status) => {
    expect(recordingGistLabel({ gist: "  Planning the launch.  ", status })).toBe(
      "Summary of transcript so far: Planning the launch.",
    );
  });

  it("keeps a live client provisional and removes the prefix once it settles", () => {
    const recording = { gist: "Launch planning.", status: RecordingStatus.PROCESSED };
    expect(recordingGistLabel({ ...recording, client_status: ClientStatus.RECORDING })).toBe(
      "Summary of transcript so far: Launch planning.",
    );
    expect(recordingTitleHint(recording, "Click to rename")).toBe("Launch planning.\n\nClick to rename");
  });
});
