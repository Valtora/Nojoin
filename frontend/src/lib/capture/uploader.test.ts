import { AxiosError } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  holdSignInInPlace,
  noteSessionRefusal,
  noteSignedIn,
  resetSessionForTests,
} from "@/lib/session";

import { MemorySegmentStore, type StoredSegment } from "./segmentStore";
import { createSegmentUploader } from "./uploader";

/** Mirrors what uploadRecordingSegment resolves to on a successful upload. */
const accepted = (sequence: number) => ({ status: "ok", segment: sequence });

describe("capture uploader", () => {
  it("reports how much it is still holding", async () => {
    // The coverage check subtracts this, so a stalled server reads as a queue
    // rather than as lost audio.
    const uploadSegment = vi.fn(async () => {
      await new Promise(() => {});
      return accepted(0);
    });
    const uploader = createSegmentUploader({
      recordingId: "rec-1",
      uploadSegment: uploadSegment as never,
    });

    expect(uploader.pendingSegmentCount()).toBe(0);
    uploader.enqueue(0, new Blob(["a"]));
    uploader.enqueue(1, new Blob(["b"]));

    expect(uploader.pendingSegmentCount()).toBe(2);
    uploader.dispose();
  });

  it("uploads queued segments in sequence and retries with backoff", async () => {
    const attempts: number[] = [];
    const uploaded: number[] = [];
    const wait = vi.fn(async () => {});
    const uploadSegment = vi.fn(async (_recordingId: string, sequence: number) => {
      attempts.push(sequence);

      if (sequence === 0 && attempts.filter((value) => value === 0).length === 1) {
        throw new Error("retry first segment");
      }

      return accepted(sequence);
    });

    const uploader = createSegmentUploader({
      recordingId: "42",
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
      recordingId: "7",
      uploadSegment,
      wait,
      retryDelaysMs: [10, 20],
      onStalled,
    });

    uploader.enqueue(0, new Blob(["first"]));
    uploader.enqueue(1, new Blob(["second"]));

    await expect(uploader.waitForIdle()).rejects.toThrow("stalled upload failure");

    expect(uploadSegment).toHaveBeenCalledTimes(3);
    expect(uploadSegment).toHaveBeenNthCalledWith(1, "7", 0, expect.any(Blob));
    expect(uploadSegment).toHaveBeenNthCalledWith(2, "7", 0, expect.any(Blob));
    expect(uploadSegment).toHaveBeenNthCalledWith(3, "7", 0, expect.any(Blob));
    expect(wait).toHaveBeenCalledTimes(2);
    expect(onStalled).toHaveBeenCalledTimes(1);
    expect(onStalled).toHaveBeenCalledWith(stallError);
  });

  it("recovers from a stall and uploads the retained segments", async () => {
    const uploaded: number[] = [];
    const onStalled = vi.fn(async () => {});
    const wait = vi.fn(async () => {});
    let networkDown = true;
    const uploadSegment = vi.fn(async (_recordingId: string, sequence: number) => {
      if (networkDown) {
        throw new Error("network outage");
      }
      uploaded.push(sequence);

      return accepted(sequence);
    });

    const uploader = createSegmentUploader({
      recordingId: "7",
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
      recordingId: "7",
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
      recordingId: "7",
      uploadSegment: vi.fn(async (_recordingId: string, sequence: number) =>
        accepted(sequence),
      ),
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
      recordingId: "7",
      uploadSegment: vi.fn(async (_recordingId: string, sequence: number) =>
        accepted(sequence),
      ),
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
      recordingId: "7",
      uploadSegment: vi.fn(async (_recordingId: string, sequence: number) =>
        accepted(sequence),
      ),
      onUploaded: (sequence) => uploaded.push(sequence),
    });

    uploader.enqueue(0, new Blob(["first"]));
    uploader.enqueue(1, new Blob(["second"]));

    await uploader.waitForIdle({ timeoutMs: 5_000 });

    expect(uploaded).toEqual([0, 1]);
  });
});

describe("capture uploader with a segment store", () => {
  const storeWith = (initial: StoredSegment[] = []) => {
    const store = new MemorySegmentStore();
    for (const segment of initial) {
      void store.put(segment);
    }
    return store;
  };

  it("keeps each segment in the store until the server acknowledges it", async () => {
    const store = storeWith();
    let release: () => void = () => {};
    const uploadSegment = vi.fn(
      () =>
        new Promise<ReturnType<typeof accepted>>((resolve) => {
          release = () => resolve(accepted(0));
        }),
    );
    const uploader = createSegmentUploader({
      recordingId: "rec-1",
      uploadSegment: uploadSegment as never,
      store,
    });

    uploader.enqueue(0, new Blob(["a"]), 2);
    await vi.waitFor(() => expect(uploadSegment).toHaveBeenCalledTimes(1));

    expect((await store.list("rec-1")).map((segment) => segment.sequence)).toEqual([0]);
    release();
    await vi.waitFor(async () => expect(await store.list("rec-1")).toEqual([]));
  });

  it("leaves the store alone when the page goes away", async () => {
    const store = storeWith();
    const uploader = createSegmentUploader({
      recordingId: "rec-1",
      uploadSegment: vi.fn(() => new Promise(() => {})) as never,
      store,
    });

    uploader.enqueue(0, new Blob(["a"]), 2);
    uploader.enqueue(1, new Blob(["b"]), 2);
    uploader.dispose();

    await vi.waitFor(async () =>
      expect((await store.list("rec-1")).map((segment) => segment.sequence)).toEqual([0, 1]),
    );
  });

  it("uploads restored segments first, without writing them again", async () => {
    const restored: StoredSegment[] = [
      { recordingId: "rec-1", sequence: 5, blob: new Blob(["5"]), durationSeconds: 2 },
      { recordingId: "rec-1", sequence: 6, blob: new Blob(["6"]), durationSeconds: 1.5 },
    ];
    const store = storeWith(restored);
    const put = vi.spyOn(store, "put");
    const uploaded: number[] = [];
    const uploader = createSegmentUploader({
      recordingId: "rec-1",
      initialSequence: 5,
      uploadSegment: vi.fn(async (_id: string, sequence: number) => {
        uploaded.push(sequence);
        return accepted(sequence);
      }) as never,
      store,
    });

    uploader.restore(restored);
    uploader.enqueue(7, new Blob(["7"]), 2);
    await uploader.waitForIdle();

    expect(uploaded).toEqual([5, 6, 7]);
    expect(put.mock.calls.map(([segment]) => segment.sequence)).toEqual([7]);
    await vi.waitFor(async () => expect(await store.list("rec-1")).toEqual([]));
  });

  it("reports the exact audio it holds as the queue changes", async () => {
    const sizes: { segments: number; seconds: number }[] = [];
    const uploader = createSegmentUploader({
      recordingId: "rec-1",
      uploadSegment: vi.fn(async (_id: string, sequence: number) => accepted(sequence)) as never,
      onQueueChange: (size) => sizes.push(size),
    });

    uploader.enqueue(0, new Blob(["a"]), 2);
    uploader.enqueue(1, new Blob(["b"]), 0.75);

    expect(uploader.pendingAudioSeconds()).toBe(2.75);
    await uploader.waitForIdle();
    expect(sizes.at(-1)).toEqual({ segments: 0, seconds: 0 });
    expect(sizes).toContainEqual({ segments: 2, seconds: 2.75 });
  });
});

describe("capture uploader when the session ends", () => {
  beforeEach(() => {
    resetSessionForTests();
  });

  afterEach(() => {
    resetSessionForTests();
  });

  const sessionEnded = () => {
    const error = new AxiosError("Request failed with status code 401");
    Object.assign(error, { response: { status: 401, data: { detail: "Not authenticated" } } });
    return error;
  };

  it("holds every segment until the user signs back in, without stalling", async () => {
    noteSignedIn({ username: "alice", force_password_change: false });
    holdSignInInPlace(true);

    let signedIn = true;
    const uploaded: number[] = [];
    const onStalled = vi.fn(async () => {});
    const wait = vi.fn(async () => {});
    const uploadSegment = vi.fn(async (_id: string, sequence: number) => {
      if (!signedIn) {
        // What the API client's interceptor does with the refusal.
        const error = sessionEnded();
        noteSessionRefusal(error);
        throw error;
      }
      uploaded.push(sequence);
      return accepted(sequence);
    });
    const uploader = createSegmentUploader({
      recordingId: "rec-1",
      uploadSegment: uploadSegment as never,
      wait,
      retryDelaysMs: [10, 20],
      onStalled,
    });

    uploader.enqueue(0, new Blob(["a"]), 2);
    await uploader.waitForIdle();
    signedIn = false;

    // Recording carries on while the session is gone; the audio queues.
    uploader.enqueue(1, new Blob(["b"]), 2);
    uploader.enqueue(2, new Blob(["c"]), 2);
    uploader.enqueue(3, new Blob(["d"]), 2);
    await vi.waitFor(() => expect(uploadSegment).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(uploadSegment).toHaveBeenCalledTimes(2);
    expect(uploader.pendingAudioSeconds()).toBe(6);
    expect(onStalled).not.toHaveBeenCalled();
    expect(wait).not.toHaveBeenCalled();

    signedIn = true;
    noteSignedIn({ username: "alice", force_password_change: false });
    await uploader.waitForIdle();

    expect(uploaded).toEqual([0, 1, 2, 3]);
    expect(onStalled).not.toHaveBeenCalled();
  });

  it("treats a refusal as an ordinary failure when nothing holds sign-in in place", async () => {
    const onStalled = vi.fn(async () => {});
    const uploader = createSegmentUploader({
      recordingId: "rec-1",
      uploadSegment: vi.fn(async () => {
        throw sessionEnded();
      }) as never,
      wait: vi.fn(async () => {}),
      retryDelaysMs: [10],
      onStalled,
    });

    uploader.enqueue(0, new Blob(["a"]), 2);

    await expect(uploader.waitForIdle()).rejects.toMatchObject({
      response: { status: 401 },
    });
    expect(onStalled).toHaveBeenCalledTimes(1);
  });

  it("stops waiting once disposed", async () => {
    let release = () => {};
    const uploadSegment = vi.fn(async () => {
      throw sessionEnded();
    });
    const uploader = createSegmentUploader({
      recordingId: "rec-1",
      uploadSegment: uploadSegment as never,
      waitForSignIn: () =>
        new Promise<boolean>((resolve) => {
          release = () => resolve(true);
        }),
    });

    uploader.enqueue(0, new Blob(["a"]), 2);
    await vi.waitFor(() => expect(uploadSegment).toHaveBeenCalledTimes(1));
    uploader.dispose();
    release();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(uploadSegment).toHaveBeenCalledTimes(1);
  });
});
