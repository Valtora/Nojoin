import { describe, expect, it, vi } from "vitest";

import { createSegmentUploader } from "./uploader";

describe("capture uploader", () => {
  it("uploads queued segments in sequence and retries with backoff", async () => {
    const attempts: number[] = [];
    const uploaded: number[] = [];
    const wait = vi.fn(async () => {});
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

  it("stalls after retries are exhausted and keeps queued segments", async () => {
    const stallError = new Error("stalled upload failure");
    const onStalled = vi.fn(async () => {});
    const wait = vi.fn(async () => {});
    const uploadSegment = vi.fn(async () => {
      throw stallError;
    });

    const uploader = createSegmentUploader({
      recordingId: 7,
      uploadSegment,
      wait,
      retryDelaysMs: [10, 20],
      onStalled,
    });

    uploader.enqueue(0, new Blob(["first"]));
    uploader.enqueue(1, new Blob(["second"]));

    await expect(uploader.waitForIdle()).rejects.toThrow("stalled upload failure");

    expect(uploadSegment).toHaveBeenCalledTimes(3);
    expect(uploadSegment).toHaveBeenNthCalledWith(1, 7, 0, expect.any(Blob));
    expect(uploadSegment).toHaveBeenNthCalledWith(2, 7, 0, expect.any(Blob));
    expect(uploadSegment).toHaveBeenNthCalledWith(3, 7, 0, expect.any(Blob));
    expect(wait).toHaveBeenCalledTimes(2);
    expect(onStalled).toHaveBeenCalledTimes(1);
    expect(onStalled).toHaveBeenCalledWith(stallError);
  });

  it("recovers from a stall and uploads the retained segments", async () => {
    const uploaded: number[] = [];
    const onStalled = vi.fn(async () => {});
    const wait = vi.fn(async () => {});
    let networkDown = true;
    const uploadSegment = vi.fn(async (_recordingId: number, sequence: number) => {
      if (networkDown) {
        throw new Error("network outage");
      }
      uploaded.push(sequence);
    });

    const uploader = createSegmentUploader({
      recordingId: 7,
      uploadSegment,
      wait,
      retryDelaysMs: [10],
      onStalled,
    });

    uploader.enqueue(0, new Blob(["first"]));
    uploader.enqueue(1, new Blob(["second"]));

    await expect(uploader.waitForIdle()).rejects.toThrow("network outage");
    expect(onStalled).toHaveBeenCalledTimes(1);

    // Segments queued while stalled are retained rather than dropped.
    uploader.enqueue(2, new Blob(["third"]));

    networkDown = false;
    expect(uploader.recover()).toBe(true);

    await uploader.waitForIdle();

    expect(uploaded).toEqual([0, 1, 2]);
  });

  it("refuses to recover once disposed", () => {
    const uploader = createSegmentUploader({
      recordingId: 7,
      uploadSegment: vi.fn(),
    });

    uploader.dispose();

    expect(uploader.recover()).toBe(false);
  });

  it("gives up waiting when a queued sequence can never arrive", async () => {
    // A gap means the segment for nextExpectedSequence was dropped, so waiting
    // is futile. An unbounded wait here stopped stop() ever calling finalize.
    let clock = 0;
    const wait = vi.fn(async (ms: number) => {
      clock += ms;
    });

    const uploader = createSegmentUploader({
      recordingId: 7,
      uploadSegment: vi.fn(async () => {}),
      wait,
      now: () => clock,
    });

    uploader.enqueue(3, new Blob(["out of order"]));

    await expect(
      uploader.waitForIdle({ timeoutMs: 500 }),
    ).rejects.toMatchObject({
      name: "UploaderTimeoutError",
      expectedSequence: 0,
      pendingSequences: [3],
    });
  });

  it("yields to macrotasks rather than starving the event loop on a gap", async () => {
    // The gap branch previously awaited Promise.resolve(), a microtask, which
    // starved timers and network callbacks and locked up the tab.
    let clock = 0;
    const wait = vi.fn(async (ms: number) => {
      clock += ms;
    });

    const uploader = createSegmentUploader({
      recordingId: 7,
      uploadSegment: vi.fn(async () => {}),
      wait,
      now: () => clock,
    });

    uploader.enqueue(2, new Blob(["out of order"]));

    await expect(uploader.waitForIdle({ timeoutMs: 200 })).rejects.toThrow();

    expect(wait).toHaveBeenCalled();
    expect(wait.mock.calls.every(([ms]) => ms > 0)).toBe(true);
  });

  it("still drains cleanly within its deadline", async () => {
    const uploaded: number[] = [];
    const uploader = createSegmentUploader({
      recordingId: 7,
      uploadSegment: vi.fn(async () => {}),
      onUploaded: (sequence) => uploaded.push(sequence),
    });

    uploader.enqueue(0, new Blob(["first"]));
    uploader.enqueue(1, new Blob(["second"]));

    await uploader.waitForIdle({ timeoutMs: 5_000 });

    expect(uploaded).toEqual([0, 1]);
  });
});
