import { afterEach, describe, expect, it, vi } from "vitest";

import { createBrowserRecorder } from "./recorder";

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];

  static isTypeSupported(mimeType: string) {
    return mimeType === "audio/webm;codecs=opus";
  }

  ondataavailable: ((event: BlobEvent) => void) | null = null;

  onerror: (() => void) | null = null;

  onstop: (() => void) | null = null;

  state: RecordingState = "inactive";

  readonly mimeType: string;

  readonly audioBitsPerSecond: number | undefined;

  constructor(_stream: MediaStream, options?: MediaRecorderOptions) {
    this.mimeType = options?.mimeType ?? "audio/webm;codecs=opus";
    this.audioBitsPerSecond = options?.audioBitsPerSecond;
    FakeMediaRecorder.instances.push(this);
  }

  start() {
    this.state = "recording";
  }

  stop() {
    if (this.state === "inactive") {
      return;
    }

    this.state = "inactive";
    const index = FakeMediaRecorder.instances.indexOf(this);
    const webmHeader = new Uint8Array([0x1a, 0x45, 0xdf, 0xa3]);
    const payload = new Blob([webmHeader, `segment-${index}`], {
      type: this.mimeType,
    });
    this.ondataavailable?.({ data: payload } as BlobEvent);
    this.onstop?.();
  }

  requestData() {}
}

describe("browser recorder", () => {
  afterEach(() => {
    FakeMediaRecorder.instances = [];
    vi.useRealTimers();
  });

  it("emits independently closed WebM blobs for each timeslice", async () => {
    vi.useFakeTimers();
    const chunks: { sequence: number; blob: Blob }[] = [];
    const recorder = createBrowserRecorder({
      stream: {} as MediaStream,
      mediaRecorderCtor: FakeMediaRecorder as unknown as typeof MediaRecorder,
      timesliceMs: 1_000,
      onChunk: (chunk) => {
        chunks.push(chunk);
      },
    });

    recorder.start(1_000);

    await vi.advanceTimersByTimeAsync(1_000);
    await vi.waitFor(() => expect(chunks).toHaveLength(1));

    await vi.advanceTimersByTimeAsync(1_000);
    await vi.waitFor(() => expect(chunks).toHaveLength(2));

    await recorder.stop({ emitTail: true });

    expect(chunks.map((chunk) => chunk.sequence)).toEqual([0, 1, 2]);
    expect(FakeMediaRecorder.instances).toHaveLength(3);
    expect(
      FakeMediaRecorder.instances.map((instance) => instance.audioBitsPerSecond),
    ).toEqual([160_000, 160_000, 160_000]);

    for (const chunk of chunks) {
      expect(chunk.blob.type).toBe("audio/webm;codecs=opus");
      const header = new Uint8Array(await chunk.blob.slice(0, 4).arrayBuffer());
      expect(Array.from(header)).toEqual([0x1a, 0x45, 0xdf, 0xa3]);
    }
  });

  it("drops the current in-memory tail when stopping for a guarded exit", async () => {
    const chunks: { sequence: number; blob: Blob }[] = [];
    const recorder = createBrowserRecorder({
      stream: {} as MediaStream,
      mediaRecorderCtor: FakeMediaRecorder as unknown as typeof MediaRecorder,
      onChunk: (chunk) => {
        chunks.push(chunk);
      },
    });

    recorder.start();
    await recorder.stop({ emitTail: false });

    expect(chunks).toEqual([]);
  });
});