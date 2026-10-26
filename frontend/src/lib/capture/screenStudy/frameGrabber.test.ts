import { afterEach, describe, expect, it, vi } from "vitest";

import { createWorkerFrameGrabber, FRAME_STREAM_ENDED_MESSAGE } from "./frameGrabber";

class FakeWorker {
  posted: Array<{ message: Record<string, unknown>; transfer?: Transferable[] }> = [];

  terminated = false;

  onmessage: ((event: MessageEvent) => void) | null = null;

  onerror: ((event: Event) => void) | null = null;

  postMessage(message: Record<string, unknown>, transfer?: Transferable[]) {
    this.posted.push({ message, transfer });
  }

  terminate() {
    this.terminated = true;
  }

  reply(data: Record<string, unknown>) {
    this.onmessage?.({ data } as MessageEvent);
  }

  lastId() {
    return this.posted.at(-1)?.message.id as number;
  }
}

const fakeTrack = () => ({ stop: vi.fn() }) as unknown as MediaStreamTrack & { stop: ReturnType<typeof vi.fn> };

describe("worker frame grabber", () => {
  it("hands the worker the processor's readable side of the clone", () => {
    const worker = new FakeWorker();
    const readable = { locked: false };
    const Processor = vi.fn(function (this: { readable: unknown }) {
      this.readable = readable;
    });
    const clone = fakeTrack();

    createWorkerFrameGrabber(clone, {
      createWorker: () => worker,
      trackProcessor: Processor as never,
    });

    expect(Processor).toHaveBeenCalledWith({ track: clone });
    expect(worker.posted[0]).toEqual({
      message: { type: "start", readable },
      transfer: [readable],
    });
  });

  it("hands over the clone itself where only a worker has the processor", () => {
    const worker = new FakeWorker();
    const clone = fakeTrack();
    createWorkerFrameGrabber(clone, { createWorker: () => worker, trackProcessor: null });
    expect(worker.posted[0].message).toEqual({ type: "start-track", track: clone });
  });

  it("answers a sample with the worker's grid, and null before the first frame", async () => {
    const worker = new FakeWorker();
    const grabber = createWorkerFrameGrabber(fakeTrack(), {
      createWorker: () => worker,
      trackProcessor: null,
    });

    const empty = grabber.sample();
    worker.reply({ type: "sample", id: worker.lastId(), empty: true });
    await expect(empty).resolves.toBeNull();

    const grid = new Uint8Array([1, 2, 3]);
    const sample = grabber.sample();
    worker.reply({
      type: "sample",
      id: worker.lastId(),
      grid,
      width: 1280,
      height: 720,
      timestamp: 4_000_123,
      grabbedAt: 1_761_400_000_000.5,
    });
    await expect(sample).resolves.toEqual({
      grid,
      width: 1280,
      height: 720,
      timestampUs: 4_000_123,
      grabbedAt: 1_761_400_000_000.5,
    });

    const blob = new Blob(["jpeg"], { type: "image/jpeg" });
    const encoded = grabber.encode();
    worker.reply({ type: "jpeg", id: worker.lastId(), blob });
    await expect(encoded).resolves.toBe(blob);
  });

  it("fails every request once the worker reports an error", async () => {
    const worker = new FakeWorker();
    const grabber = createWorkerFrameGrabber(fakeTrack(), {
      createWorker: () => worker,
      trackProcessor: null,
    });

    const pending = grabber.sample();
    worker.reply({ type: "error", message: "The shared screen could not be read" });

    await expect(pending).rejects.toThrow("The shared screen could not be read");
    await expect(grabber.encode()).rejects.toThrow("The shared screen could not be read");
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("fails every request once the shared video ends", async () => {
    const worker = new FakeWorker();
    const grabber = createWorkerFrameGrabber(fakeTrack(), {
      createWorker: () => worker,
      trackProcessor: null,
    });

    worker.reply({ type: "ended" });

    await expect(grabber.sample()).rejects.toThrow(FRAME_STREAM_ENDED_MESSAGE);
  });

  it("stops the clone at once and lets the worker close its stream first", () => {
    vi.useFakeTimers();
    const worker = new FakeWorker();
    const clone = fakeTrack();
    const grabber = createWorkerFrameGrabber(clone, {
      createWorker: () => worker,
      trackProcessor: null,
    });

    grabber.stop();
    grabber.stop();

    expect(clone.stop).toHaveBeenCalledTimes(1);
    expect(worker.posted.at(-1)?.message).toEqual({ type: "stop" });
    expect(worker.terminated).toBe(false);
    worker.reply({ type: "stopped" });
    expect(worker.terminated).toBe(true);
  });

  it("ends a worker that does not answer stop within a second", () => {
    vi.useFakeTimers();
    const worker = new FakeWorker();
    const grabber = createWorkerFrameGrabber(fakeTrack(), {
      createWorker: () => worker,
      trackProcessor: null,
    });

    grabber.stop();
    vi.advanceTimersByTime(999);
    expect(worker.terminated).toBe(false);
    vi.advanceTimersByTime(1);
    expect(worker.terminated).toBe(true);
  });
});
