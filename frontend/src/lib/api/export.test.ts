import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getErrorStatus } from "@/lib/errors";

import api from "./client";
import { exportAudio, exportContent } from "./export";

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
  const createObjectURL = vi.fn<(blob: Blob) => string>(() => "blob:audio");

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

  it("saves the whole body when the stream answers with the audio", async () => {
    const audio = "ID3-AUDIO".repeat(1000);
    streamAnswers(200, audio, "audio/mpeg");

    await exportAudio("rec-1", "Weekly sync");

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const saved = createObjectURL.mock.calls[0][0] as Blob;
    expect(await saved.text()).toBe(audio);
  });

  it("refuses a 206, which carries only part of the audio", async () => {
    streamAnswers(206, "ID3-FIRST-CHUNK", "audio/mpeg");

    const exported = exportAudio("rec-1", "Weekly sync");

    await expect(exported).rejects.toSatisfy(
      (error: unknown) => getErrorStatus(error) === 206,
    );
    expect(createObjectURL).not.toHaveBeenCalled();
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

describe("exportContent", () => {
  beforeEach(() => {
    api.defaults.adapter = "fetch";
    window.URL.createObjectURL = vi.fn(() => "blob:export");
    window.URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("sends each section as its own key, which is how FastAPI reads a list", async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL) => Promise<Response>>(
      async () =>
        new Response("%PDF", {
          status: 200,
          headers: {
            "content-type": "application/pdf",
            "content-disposition": 'attachment; filename="20261014_Sync_FullExport.pdf"',
          },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await exportContent("rec-1", ["notes", "analytics"], "pdf", {
      SPEAKER_00: "blue",
    });

    const requested = fetchMock.mock.calls[0][0];
    const url = new URL(
      requested instanceof Request ? requested.url : String(requested),
      "http://localhost",
    );
    expect(url.pathname).toMatch(/\/transcripts\/rec-1\/export$/);
    expect(url.searchParams.getAll("sections")).toEqual(["notes", "analytics"]);
    expect(url.searchParams.get("export_format")).toBe("pdf");
    expect(JSON.parse(url.searchParams.get("speaker_colors") ?? "{}")).toEqual({
      SPEAKER_00: "blue",
    });
  });
});
