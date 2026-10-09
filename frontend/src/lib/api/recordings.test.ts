import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getErrorStatus } from "@/lib/errors";

import api from "./client";
import { importAudio } from "./recordings";

// The real axios instance runs, on its fetch adapter, against a stubbed fetch.
const originalAdapter = api.defaults.adapter;

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

const recording = { id: "rec-1", name: "Screen recording", status: "QUEUED" };

/** Answers init and the segment upload, then each finalize call in turn. */
const serveImport = (finalizeAnswers: Array<() => Response>) => {
  const finalizeCalls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes("/import/chunked/finalize")) {
        finalizeCalls.push(url);
        const answer = finalizeAnswers[finalizeCalls.length - 1];
        return answer ? answer() : json(500, { detail: "unexpected call" });
      }
      return json(200, { ...recording, status: "UPLOADING" });
    }),
  );
  return finalizeCalls;
};

const upload = () =>
  importAudio(new File(["video"], "screen.mkv", { type: "video/x-matroska" }));

describe("importAudio finalize", () => {
  beforeEach(() => {
    api.defaults.adapter = "fetch";
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("retries when a proxy gave up before the server answered", async () => {
    const finalizeCalls = serveImport([
      () => json(524, { detail: "A timeout occurred" }),
      () => json(502, { detail: "Bad gateway" }),
      () => json(200, recording),
    ]);

    await expect(upload()).resolves.toMatchObject({ id: "rec-1" });
    expect(finalizeCalls).toHaveLength(3);
  });

  it("does not retry the server's own answer", async () => {
    const finalizeCalls = serveImport([
      () =>
        json(422, {
          detail: "Nojoin could not extract this file's audio track.",
        }),
      () => json(200, recording),
    ]);

    await expect(upload()).rejects.toSatisfy(
      (error: unknown) => getErrorStatus(error) === 422,
    );
    expect(finalizeCalls).toHaveLength(1);
  });

  it("gives up after five lost answers", async () => {
    const finalizeCalls = serveImport(
      Array.from(
        { length: 6 },
        () => () => json(504, { detail: "Gateway timeout" }),
      ),
    );

    await expect(upload()).rejects.toSatisfy(
      (error: unknown) => getErrorStatus(error) === 504,
    );
    expect(finalizeCalls).toHaveLength(5);
  });
});
