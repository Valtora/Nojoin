// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { FRAME_QUEUE_CAPACITY, FrameQueue, type QueuedFrame } from "./frameQueue";

const frame = (sequence: number): QueuedFrame => ({
  sequence,
  offsetMs: 0,
  blob: new Blob([`frame-${sequence}`], { type: "image/jpeg" }),
  tick: sequence,
  frameTsUs: sequence * 1_000_000,
  grabDelayMs: 10,
});

const deferred = <T>() => {
  let resolve: (value: T) => void = () => {};
  let reject: (error: unknown) => void = () => {};
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
};

const flush = async () => {
  for (let index = 0; index < 5; index += 1) {
    await Promise.resolve();
  }
};

describe("screen study frame queue", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends one frame at a time, in order", async () => {
    const sends: Array<{ sequence: number; done: ReturnType<typeof deferred<string>> }> = [];
    const queue = new FrameQueue<string>({
      send: (item) => {
        const done = deferred<string>();
        sends.push({ sequence: item.sequence, done });
        return done.promise;
      },
      audioWaiting: () => false,
    });

    queue.enqueue(frame(1));
    queue.enqueue(frame(2));
    queue.enqueue(frame(3));
    expect(sends.map((send) => send.sequence)).toEqual([1]);

    sends[0].done.resolve("ok");
    await flush();
    expect(sends.map((send) => send.sequence)).toEqual([1, 2]);
    sends[1].done.resolve("ok");
    await flush();
    expect(sends.map((send) => send.sequence)).toEqual([1, 2, 3]);
  });

  it("holds every frame while audio is waiting, and goes once it has gone", async () => {
    let audioWaiting = true;
    const send = vi.fn<(item: QueuedFrame) => Promise<string>>(async () => "ok");
    const queue = new FrameQueue<string>({ send, audioWaiting: () => audioWaiting });

    queue.enqueue(frame(1));
    queue.enqueue(frame(2));
    queue.audioQueueChanged();
    expect(send).not.toHaveBeenCalled();
    expect(queue.state.queued).toBe(2);

    audioWaiting = false;
    queue.audioQueueChanged();
    await flush();

    expect(send.mock.calls.map(([item]) => item.sequence)).toEqual([1, 2]);
  });

  it("checks for audio before each frame, so a new segment goes first", async () => {
    let audioWaiting = false;
    const send = vi.fn(async () => {
      // A segment arrives while this frame uploads.
      audioWaiting = true;
      return "ok";
    });
    const queue = new FrameQueue<string>({ send, audioWaiting: () => audioWaiting });

    queue.enqueue(frame(1));
    queue.enqueue(frame(2));
    await flush();

    expect(send).toHaveBeenCalledTimes(1);
    audioWaiting = false;
    queue.audioQueueChanged();
    await flush();
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("holds at most 30 frames and drops the oldest", () => {
    const send = vi.fn(async () => "ok");
    const queue = new FrameQueue<string>({ send, audioWaiting: () => true });

    for (let sequence = 0; sequence < FRAME_QUEUE_CAPACITY + 5; sequence += 1) {
      queue.enqueue(frame(sequence));
    }

    expect(queue.state).toMatchObject({ queued: FRAME_QUEUE_CAPACITY, dropped: 5 });
  });

  it("stops for good after a failed upload and says so", async () => {
    const onFailed = vi.fn();
    const failure = new Error("refused");
    const send = vi.fn().mockRejectedValueOnce(failure).mockResolvedValue("ok");
    const queue = new FrameQueue<string>({ send, audioWaiting: () => false, onFailed });

    queue.enqueue(frame(1));
    queue.enqueue(frame(2));
    await flush();
    queue.enqueue(frame(3));
    await flush();

    expect(onFailed).toHaveBeenCalledWith(failure, expect.objectContaining({ tick: 1 }));
    expect(send).toHaveBeenCalledTimes(1);
    expect(queue.state).toMatchObject({ failed: true, queued: 0 });
  });

  it("drains within its time limit and gives up after it", async () => {
    vi.useFakeTimers();
    let audioWaiting = false;
    const queue = new FrameQueue<string>({
      send: async () => "ok",
      audioWaiting: () => audioWaiting,
    });
    queue.enqueue(frame(1));
    await expect(queue.drain(20_000)).resolves.toBe(true);

    audioWaiting = true;
    queue.enqueue(frame(2));
    const drained = queue.drain(20_000);
    await vi.advanceTimersByTimeAsync(20_000);
    await expect(drained).resolves.toBe(false);
    expect(queue.state.queued).toBe(1);
  });

  it("sends nothing more once disposed", async () => {
    const send = vi.fn(async () => "ok");
    const queue = new FrameQueue<string>({ send, audioWaiting: () => true });
    queue.enqueue(frame(1));
    queue.dispose();
    queue.audioQueueChanged();
    queue.enqueue(frame(2));
    await flush();
    expect(send).not.toHaveBeenCalled();
    expect(queue.state.queued).toBe(0);
  });

  it("reports every frame it drops, from the full queue and at dispose", () => {
    const onDropped = vi.fn();
    const queue = new FrameQueue<string>({
      send: async () => "ok",
      audioWaiting: () => true,
      onDropped,
      capacity: 2,
    });
    queue.enqueue(frame(1));
    queue.enqueue(frame(2));
    queue.enqueue(frame(3));
    expect(onDropped.mock.calls.map(([item]) => item.tick)).toEqual([1]);

    queue.dispose();
    expect(onDropped.mock.calls.map(([item]) => item.tick)).toEqual([1, 2, 3]);
  });

  it("aborts the frame in flight when disposed, and counts it dropped, not failed", async () => {
    const onDropped = vi.fn();
    const onFailed = vi.fn();
    let signal: AbortSignal | null = null;
    const queue = new FrameQueue<string>({
      send: (_item, abort) =>
        new Promise<string>((_resolve, reject) => {
          signal = abort;
          abort.addEventListener("abort", () => reject(new Error("aborted")));
        }),
      audioWaiting: () => false,
      onDropped,
      onFailed,
    });
    queue.enqueue(frame(7));
    expect(signal).not.toBeNull();

    queue.dispose();
    await expect(queue.drain(1_000)).resolves.toBe(true);

    expect((signal as AbortSignal | null)?.aborted).toBe(true);
    expect(onFailed).not.toHaveBeenCalled();
    expect(onDropped.mock.calls.map(([item]) => item.tick)).toEqual([7]);
  });
});
