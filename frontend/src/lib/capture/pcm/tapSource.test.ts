import { afterEach, describe, expect, it, vi } from "vitest";

import { createPortPcmSource, type PcmTapPort } from "./tapSource";

class FakePort implements PcmTapPort {
  sent: unknown[] = [];

  onmessage: ((event: MessageEvent) => void) | null = null;

  postMessage(message: unknown) {
    this.sent.push(message);
  }

  deliver(data: unknown) {
    this.onmessage?.({ data } as MessageEvent);
  }
}

describe("PCM tap source", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("hands each block to every subscriber until it unsubscribes", () => {
    const port = new FakePort();
    const source = createPortPcmSource(port, 48_000, 2);
    const first = vi.fn();
    const second = vi.fn();
    const unsubscribe = source.subscribe(first);
    source.subscribe(second);
    const channels = [new Float32Array(4), new Float32Array(4)];

    port.deliver({ type: "pcm", channels });
    unsubscribe();
    port.deliver({ type: "pcm", channels });

    expect(first).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenCalledWith(channels);
    expect(second).toHaveBeenCalledTimes(2);
  });

  it("resolves a flush once the worklet acknowledges it", async () => {
    const port = new FakePort();
    const source = createPortPcmSource(port, 48_000, 2);

    const flushed = source.flush();
    const request = port.sent.at(-1) as { type: string; id: number };
    port.deliver({ type: "flushed", id: request.id });

    expect(request.type).toBe("flush");
    await expect(flushed).resolves.toBe(true);
  });

  it("gives up on a flush the audio thread never answers", async () => {
    vi.useFakeTimers();
    const source = createPortPcmSource(new FakePort(), 48_000, 2);

    const flushed = source.flush(500);
    vi.advanceTimersByTime(500);

    await expect(flushed).resolves.toBe(false);
  });

  it("stops the worklet and settles pending flushes on close", async () => {
    const port = new FakePort();
    const source = createPortPcmSource(port, 48_000, 2);
    const flushed = source.flush();

    source.close();

    expect(port.sent.at(-1)).toEqual({ type: "stop" });
    await expect(flushed).resolves.toBe(false);
    await expect(source.flush()).resolves.toBe(false);
  });
});
