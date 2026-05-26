import { describe, expect, it, vi } from "vitest";

import { createSegmentUploader } from "./uploader";

describe("capture uploader", () => {
  it("uploads queued segments in sequence and retries with backoff", async () => {
    const attempts: number[] = [];
    const uploaded: number[] = [];
    const wait = vi.fn(async (_ms: number) => {});
    const uploadSegment = vi.fn(async (_recordingId: number, sequence: number) => {
      attempts.push(sequence);

      if (sequence === 0 && attempts.filter((value) => value === 0).length === 1) {
        throw new Error("retry first segment");
      }
    });

    const uploader = createSegmentUploader({
      recordingId: 42,
      uploadSegment,
      wait,
      retryDelaysMs: [25, 50],
      onUploaded: (sequence) => uploaded.push(sequence),
    });

    uploader.enqueue(1, new Blob(["second"]));
    uploader.enqueue(0, new Blob(["first"]));

    await vi.waitFor(() => {
      expect(attempts).toEqual([0, 0, 1]);
      expect(uploaded).toEqual([0, 1]);
    });

    expect(wait).toHaveBeenCalledTimes(1);
    expect(wait).toHaveBeenCalledWith(25);
  });

  it("surfaces a fatal error after retries are exhausted and stops draining", async () => {
    const fatalError = new Error("fatal upload failure");
    const onFatal = vi.fn(async (_error: Error) => {});
    const wait = vi.fn(async (_ms: number) => {});
    const uploadSegment = vi.fn(async () => {
      throw fatalError;
    });

    const uploader = createSegmentUploader({
      recordingId: 7,
      uploadSegment,
      wait,
      retryDelaysMs: [10, 20],
      onFatal,
    });

    uploader.enqueue(0, new Blob(["first"]));
    uploader.enqueue(1, new Blob(["second"]));

    await expect(uploader.waitForIdle()).rejects.toThrow("fatal upload failure");

    expect(uploadSegment).toHaveBeenCalledTimes(3);
    expect(uploadSegment).toHaveBeenNthCalledWith(1, 7, 0, expect.any(Blob));
    expect(uploadSegment).toHaveBeenNthCalledWith(2, 7, 0, expect.any(Blob));
    expect(uploadSegment).toHaveBeenNthCalledWith(3, 7, 0, expect.any(Blob));
    expect(wait).toHaveBeenCalledTimes(2);
    expect(onFatal).toHaveBeenCalledTimes(1);
    expect(onFatal).toHaveBeenCalledWith(fatalError);
  });
});