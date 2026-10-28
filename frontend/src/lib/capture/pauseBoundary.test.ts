// @vitest-environment node

import { describe, expect, it, vi } from "vitest";
import { CaptureWorklet } from "./captureAudio.fixtures";
import { decodeFlac } from "./pcm/testFlacDecoder";
import { createPortPcmSource } from "./pcm/tapSource";
import { createBrowserRecorder, type RecordedChunk } from "./recorder";

const render = (worklet: CaptureWorklet, frames: number, left: number, right: number) => {
  for (let offset = 0; offset < frames; offset += 128) {
    worklet.render([new Float32Array(128).fill(left), new Float32Array(128).fill(right)]);
  }
};

describe("worklet pause boundary", () => {
  it("records exactly the non-paused frames and samples across every resume", async () => {
    const worklet = new CaptureWorklet();
    const source = createPortPcmSource(worklet.port, 16_000, 2);
    const chunks: RecordedChunk[] = [];
    const recorder = createBrowserRecorder({ source, onChunk: (chunk) => { chunks.push(chunk); } });
    try {
      recorder.start();
      render(worklet, 128, 0.25, -0.25);
      for (let pause = 0; pause < 2; pause += 1) {
        await recorder.pause();
        render(worklet, 4096 + 128, -0.75, 0.75);
        await recorder.resume();
        render(worklet, 4096, 0.5, -0.5);
      }
      await recorder.stop();
      const decoded = await Promise.all(chunks.map(async (chunk) =>
        decodeFlac(new Uint8Array(await chunk.blob.arrayBuffer()))));
      const samples = decoded.flatMap((chunk) => Array.from(chunk.samples));
      expect(samples.length / 2).toBe(128 + 2 * 4096);
      expect(samples).toEqual([
        ...Array.from({ length: 128 }, () => [8192, -8192]).flat(),
        ...Array.from({ length: 2 * 4096 }, () => [16384, -16384]).flat(),
      ]);
      expect(chunks.map((chunk) => chunk.sequence)).toEqual([0, 1, 2]);
      expect(chunks.reduce((sum, chunk) => sum + chunk.durationSeconds * 16_000, 0)).toBe(8320);
    } finally {
      await recorder.stop({ emitTail: false });
      source.close();
    }
  });

  it("keeps dropping PCM until the resume boundary is acknowledged", async () => {
    const worklet = new CaptureWorklet();
    const source = createPortPcmSource(worklet.port, 16_000, 2);
    const encoded: Int16Array[] = [];
    const recorder = createBrowserRecorder({
      source, onChunk: () => {},
      encode: (pcm) => { encoded.push(pcm.slice()); return new Uint8Array(pcm.buffer); },
    });
    try {
      recorder.start();
      await recorder.pause();
      render(worklet, 128, -0.75, 0.75);
      worklet.holdAcknowledgements = true;
      const resuming = recorder.resume();
      await vi.waitFor(() => expect(worklet.pendingAcknowledgements).toBe(1));
      expect(recorder.state).toBe("paused");
      render(worklet, 4096, -0.75, 0.75);
      await Promise.resolve();
      expect(encoded).toEqual([]);
      worklet.acknowledge();
      await resuming;
      expect(recorder.state).toBe("recording");
      render(worklet, 128, 0.5, -0.5);
      worklet.holdAcknowledgements = false;
      await recorder.stop();
      expect(encoded.map((pcm) => Array.from(pcm))).toEqual([
        Array.from({ length: 128 }, () => [16384, -16384]).flat(),
      ]);
    } finally {
      worklet.holdAcknowledgements = false;
      worklet.acknowledge();
      await recorder.stop({ emitTail: false });
      source.close();
    }
  });

  it("resumes when a suspended audio thread never acknowledges the boundary", async () => {
    vi.useFakeTimers();
    const worklet = new CaptureWorklet();
    const source = createPortPcmSource(worklet.port, 16_000, 2);
    const recorder = createBrowserRecorder({ source, onChunk: () => {} });
    try {
      recorder.start();
      await recorder.pause();
      worklet.holdAcknowledgements = true;
      const resuming = recorder.resume();
      await vi.advanceTimersByTimeAsync(1000);
      await resuming;
      expect(recorder.state).toBe("recording");
    } finally {
      worklet.holdAcknowledgements = false;
      await recorder.stop();
      source.close();
      vi.useRealTimers();
    }
  });

  it("settles a stop queued during resume without leaving a stall timer", async () => {
    vi.useFakeTimers();
    const worklet = new CaptureWorklet();
    const source = createPortPcmSource(worklet.port, 16_000, 2);
    const recorder = createBrowserRecorder({ source, onChunk: () => {} });
    try {
      recorder.start();
      await recorder.pause();
      worklet.holdAcknowledgements = true;
      const resuming = recorder.resume();
      await vi.advanceTimersByTimeAsync(0);
      expect(worklet.pendingAcknowledgements).toBe(1);
      const stopping = recorder.stop();
      worklet.holdAcknowledgements = false;
      worklet.acknowledge();
      await Promise.all([resuming, stopping]);
      expect(recorder.state).toBe("inactive");
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      source.close();
      vi.useRealTimers();
    }
  });
});
