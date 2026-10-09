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
const finalizing = () =>
  json(409, {
    detail: {
      code: "import_finalizing",
      message: "This import is already being finalized.",
    },
  });
const lostConnection = (): Response => {
  throw new TypeError("Failed to fetch");
};

/** Answers init and the segment upload, then each finalize call in turn
 * (the last answer repeats). Returns when each finalize call was made. */
const serveImport = (finalizeAnswers: Array<() => Response>) => {
  const finalizeCalls: number[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes("/import/chunked/finalize")) {
        finalizeCalls.push(Date.now());
        const answer =
          finalizeAnswers[
            Math.min(finalizeCalls.length, finalizeAnswers.length) - 1
          ];
        return answer();
      }
      return json(200, { ...recording, status: "UPLOADING" });
    }),
  );
  return finalizeCalls;
};

/** Runs the import to completion, firing every retry delay. */
const upload = async () => {
  const result = importAudio(
    new File(["video"], "screen.mkv", { type: "video/x-matroska" }),
  );
  const settled = result.then(
    (value) => ({ value }),
    (error: unknown) => ({ error }),
  );
  await vi.runAllTimersAsync();
  return settled;
};

const gaps = (times: number[]) => times.slice(1).map((t, i) => t - times[i]);

describe("importAudio finalize", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    api.defaults.adapter = "fetch";
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("retries, backing off, when a proxy gave up before the server answered", async () => {
    const calls = serveImport([
      () => json(524, { detail: "A timeout occurred" }),
      () => json(502, { detail: "Bad gateway" }),
      () => json(200, recording),
    ]);

    expect(await upload()).toMatchObject({ value: { id: "rec-1" } });
    expect(gaps(calls)).toEqual([2_000, 4_000]);
  });

  it("retries after a lost connection", async () => {
    const calls = serveImport([lostConnection, () => json(200, recording)]);

    expect(await upload()).toMatchObject({ value: { id: "rec-1" } });
    expect(calls).toHaveLength(2);
  });

  it("retries a 503 while the API restarts", async () => {
    const calls = serveImport([
      () => json(503, { detail: "Service unavailable" }),
      () => json(200, recording),
    ]);

    expect(await upload()).toMatchObject({ value: { id: "rec-1" } });
    expect(calls).toHaveLength(2);
  });

  it("waits out an import another call is finalizing", async () => {
    const calls = serveImport([
      () => json(524, { detail: "A timeout occurred" }),
      finalizing,
      finalizing,
      () => json(200, recording),
    ]);

    expect(await upload()).toMatchObject({ value: { id: "rec-1" } });
    expect(gaps(calls)).toEqual([2_000, 4_000, 8_000]);
  });

  it("does not retry the server's own answer", async () => {
    const calls = serveImport([
      () =>
        json(422, {
          detail: "Nojoin could not extract this file's audio track.",
        }),
      () => json(200, recording),
    ]);

    const { error } = (await upload()) as { error: unknown };
    expect(getErrorStatus(error)).toBe(422);
    expect(calls).toHaveLength(1);
  });

  it("does not wait out a 409 for missing segments", async () => {
    const calls = serveImport([
      () => json(409, { detail: "Recording upload is still in progress" }),
      () => json(200, recording),
    ]);

    const { error } = (await upload()) as { error: unknown };
    expect(getErrorStatus(error)).toBe(409);
    expect(calls).toHaveLength(1);
  });

  it("gives up after five lost answers, about 30 s of backoff", async () => {
    const calls = serveImport([() => json(504, { detail: "Gateway timeout" })]);

    const { error } = (await upload()) as { error: unknown };
    expect(getErrorStatus(error)).toBe(504);
    expect(gaps(calls)).toEqual([2_000, 4_000, 8_000, 16_000]);
  });

  it("stops waiting for a finalize after twenty minutes", async () => {
    const calls = serveImport([finalizing]);

    const { error } = (await upload()) as { error: unknown };
    expect(getErrorStatus(error)).toBe(409);
    expect(calls[calls.length - 1] - calls[0]).toBeGreaterThanOrEqual(
      20 * 60_000,
    );
    expect(Math.max(...gaps(calls))).toBe(30_000);
  });
});
