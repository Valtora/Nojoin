import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getErrorStatus } from "@/lib/errors";

import api from "./client";
import { exportAudio } from "./export";

// The real axios instance runs, on its fetch adapter, against a stubbed fetch:
// whether a status counts as success is axios's own decision.
const originalAdapter = api.defaults.adapter;

const streamAnswers = (status: number, body: string, contentType: string) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(body, { status, headers: { "content-type": contentType } }),
    ),
  );
};

describe("exportAudio", () => {
  const createObjectURL = vi.fn(() => "blob:audio");

  beforeEach(() => {
    api.defaults.adapter = "fetch";
    createObjectURL.mockClear();
    window.URL.createObjectURL = createObjectURL;
    window.URL.revokeObjectURL = vi.fn();
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("saves the file when the stream answers with the audio", async () => {
    streamAnswers(200, "ID3-AUDIO", "audio/mpeg");

    await exportAudio("rec-1", "Weekly sync");

    expect(createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("refuses a 202, whose body is a notice, instead of saving it as an .mp3", async () => {
    streamAnswers(
      202,
      JSON.stringify({ detail: "Audio proxy is being prepared." }),
      "application/json",
    );

    const exported = exportAudio("rec-1", "Weekly sync");

    await expect(exported).rejects.toSatisfy(
      (error: unknown) => getErrorStatus(error) === 202,
    );
    expect(createObjectURL).not.toHaveBeenCalled();
  });
});
