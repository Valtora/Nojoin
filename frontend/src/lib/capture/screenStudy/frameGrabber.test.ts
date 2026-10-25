import { describe, expect, it, vi } from "vitest";

import { createWorkerFrameGrabber } from "./frameGrabber";

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
    worker.reply({ type: "sample", id: worker.lastId(), grid, width: 1280, height: 720 });
    await expect(sample).resolves.toEqual({ grid, width: 1280, height: 720 });

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

  it("stops the clone and the worker, and nothing else", () => {
    const worker = new FakeWorker();
    const clone = fakeTrack();
    const grabber = createWorkerFrameGrabber(clone, {
      createWorker: () => worker,
      trackProcessor: null,
    });

    grabber.stop();
    grabber.stop();

    expect(clone.stop).toHaveBeenCalledTimes(1);
    expect(worker.terminated).toBe(true);
    expect(worker.posted.at(-1)?.message).toEqual({ type: "stop" });
  });
});
