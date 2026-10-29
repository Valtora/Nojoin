// @vitest-environment node

import { describe, expect, it } from "vitest";

import { recordingGistLabel, recordingTitleHint } from "./recordingPresentation";

describe("recording gist presentation", () => {
  it.each([undefined, null, "", "  "])("omits an unavailable gist (%s)", (gist) => {
    const recording = { gist, gist_live: true };
    expect(recordingGistLabel(recording)).toBeUndefined();
    expect(recordingTitleHint(recording, "Click to rename")).toBe("Click to rename");
  });

  it("labels a gist written during capture as the transcript so far", () => {
    expect(recordingGistLabel({ gist: "  Planning the launch.  ", gist_live: true })).toBe(
      "Summary of transcript so far: Planning the launch.",
    );
  });

  it("shows a final gist unlabelled, ahead of the title's own hint", () => {
    const recording = { gist: "Launch planning.", gist_live: false };
    expect(recordingGistLabel(recording)).toBe("Launch planning.");
    expect(recordingTitleHint(recording, "Click to rename")).toBe("Launch planning.\n\nClick to rename");
  });
});
