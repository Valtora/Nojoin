// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

import type { Recording } from "@/types";
import { createDetailTranscriptReader, type DetailTranscriptRead, type TranscriptSyncResult } from "./detailTranscriptReader";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

const result: TranscriptSyncResult = { revision: 4, segments: [], speakers: [] };
const request = (mode: "full" | "delta", sequence = 4, deferActiveEdit = true): DetailTranscriptRead => ({
  mode, options: { deferActiveEdit },
  recording: { id: "rec-1", transcript: { change_sequence: sequence } } as Recording,
});

describe("serialized detail transcript reads", () => {
  it("coalesces busy requests into a full read carrying the newest sequence and explicit edit override", async () => {
    const first = deferred<TranscriptSyncResult>();
    const second = deferred<TranscriptSyncResult>();
    const read = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const reader = createDetailTranscriptReader(read);
    const initial = reader.request(request("delta"));
    const refresh = reader.request(request("full", 5));
    const mutation = reader.request(request("delta", 6, false));
    expect(read).toHaveBeenCalledTimes(1);
    first.resolve(result);
    await initial;
    expect(read).toHaveBeenCalledTimes(2);
    expect(read.mock.calls[1][0]).toMatchObject({
      mode: "full", recording: { transcript: { change_sequence: 6 } }, options: { deferActiveEdit: false },
    });
    let mutationFinished = false;
    void mutation.then(() => { mutationFinished = true; });
    await Promise.resolve();
    expect(mutationFinished).toBe(false);
    second.resolve({ ...result, revision: 6 });
    expect(await refresh).toMatchObject({ revision: 6 });
    expect(await mutation).toMatchObject({ revision: 6 });
  });

  it("does not strand pending callers when the current read fails", async () => {
    const first = deferred<TranscriptSyncResult>();
    const read = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValueOnce(result);
    const reader = createDetailTranscriptReader(read);
    const initial = reader.request(request("delta"));
    const rejected = expect(initial).rejects.toThrow("offline");
    const pending = reader.request(request("full"));
    first.reject(new Error("offline"));
    await rejected;
    expect(await pending).toEqual(result);
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("cancels queued reads and marks a late active response unusable", async () => {
    const first = deferred<TranscriptSyncResult>();
    const read = vi.fn().mockReturnValueOnce(first.promise);
    const reader = createDetailTranscriptReader(read);
    const initial = reader.request(request("full"));
    const pending = reader.request(request("full"));
    reader.cancel();
    expect(await pending).toBeNull();
    expect(read.mock.calls[0][1]()).toBe(false);
    first.resolve(result);
    expect(await initial).toBeNull();
    expect(await reader.request(request("full"))).toBeNull();
    expect(read).toHaveBeenCalledTimes(1);
  });
});
