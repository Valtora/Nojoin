import { afterEach, describe, expect, it, vi } from "vitest";

import { createBrowserRecorder } from "./recorder";

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];

  static supportedMimeTypes = new Set(["audio/webm;codecs=opus"]);

  static isTypeSupported(mimeType: string) {
    return FakeMediaRecorder.supportedMimeTypes.has(mimeType);
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
    FakeMediaRecorder.supportedMimeTypes = new Set(["audio/webm;codecs=opus"]);
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

  it("falls back to mobile-friendly MP4 audio when WebM is unavailable", async () => {
    FakeMediaRecorder.supportedMimeTypes = new Set(["audio/mp4"]);
    const chunks: { sequence: number; blob: Blob }[] = [];
    const recorder = createBrowserRecorder({
      stream: {} as MediaStream,
      mediaRecorderCtor: FakeMediaRecorder as unknown as typeof MediaRecorder,
      onChunk: (chunk) => {
        chunks.push(chunk);
      },
    });

    recorder.start();
    await recorder.stop({ emitTail: true });

    expect(chunks).toHaveLength(1);
    expect(chunks[0].blob.type).toBe("audio/mp4");
  });

  it("resolves stop when the recorder never fires onstop", async () => {
    // Awaiting onstop unconditionally wedged the shared operation queue, so
    // stop() never reached finalize and every later control hung behind it.
    vi.useFakeTimers();

    class SilentMediaRecorder extends FakeMediaRecorder {
      stop() {
        this.state = "inactive";
        this.ondataavailable?.({
          data: new Blob(["buffered"], { type: this.mimeType }),
        } as BlobEvent);
        // Deliberately never calls onstop.
      }
    }

    const chunks: { sequence: number; blob: Blob }[] = [];
    const recorder = createBrowserRecorder({
      stream: {} as MediaStream,
      mediaRecorderCtor: SilentMediaRecorder as unknown as typeof MediaRecorder,
      stopTimeoutMs: 1_000,
      onChunk: (chunk) => {
        chunks.push(chunk);
      },
    });

    recorder.start();
    const stopPromise = recorder.stop({ emitTail: true });
    await vi.advanceTimersByTimeAsync(1_500);

    await expect(stopPromise).resolves.toBeUndefined();
    // The buffered audio is kept rather than discarded with the wedged segment.
    expect(chunks).toHaveLength(1);
    expect(recorder.state).toBe("inactive");
  });

  it("does not let a wedged segment block a later stop", async () => {
    vi.useFakeTimers();

    class SilentMediaRecorder extends FakeMediaRecorder {
      stop() {
        this.state = "inactive";
      }
    }

    const recorder = createBrowserRecorder({
      stream: {} as MediaStream,
      mediaRecorderCtor: SilentMediaRecorder as unknown as typeof MediaRecorder,
      timesliceMs: 500,
      stopTimeoutMs: 1_000,
      onChunk: () => {},
    });

    recorder.start(500);
    // Let a roll wedge on the missing onstop first.
    await vi.advanceTimersByTimeAsync(600);

    const stopPromise = recorder.stop({ emitTail: false });
    await vi.advanceTimersByTimeAsync(3_000);

    await expect(stopPromise).resolves.toBeUndefined();
  });

  it("reports a stall when no audio reaches disk while recording", async () => {
    // The failure mode has no error attached: the recorder reports "recording"
    // with an active segment while producing nothing, so silence over several
    // timeslices is the only signal available.
    vi.useFakeTimers();

    class SilentDataMediaRecorder extends FakeMediaRecorder {
      stop() {
        // Ends cleanly but hands back no audio, as a dead source track does.
        this.state = "inactive";
        this.onstop?.();
      }
    }

    const stalls: { sinceLastChunkMs: number; abandonedSegment: boolean }[] = [];
    const chunks: { sequence: number; blob: Blob }[] = [];
    const recorder = createBrowserRecorder({
      stream: {} as MediaStream,
      mediaRecorderCtor: SilentDataMediaRecorder as unknown as typeof MediaRecorder,
      timesliceMs: 2_000,
      stallTimeoutMs: 6_000,
      onStall: (info) => stalls.push(info),
      onChunk: (chunk) => {
        chunks.push(chunk);
      },
    });

    recorder.start(2_000);
    const segmentsAtStart = SilentDataMediaRecorder.instances.length;

    await vi.advanceTimersByTimeAsync(9_000);

    expect(chunks).toEqual([]);
    expect(stalls.length).toBeGreaterThanOrEqual(1);
    expect(stalls[0].abandonedSegment).toBe(false);
    // The chain keeps rolling rather than dying silently.
    expect(SilentDataMediaRecorder.instances.length).toBeGreaterThan(
      segmentsAtStart,
    );
    expect(recorder.state).toBe("recording");

    await recorder.stop({ emitTail: false });
  });

  it("does not report a stall while a healthy recorder keeps emitting", async () => {
    vi.useFakeTimers();

    const stalls: unknown[] = [];
    const chunks: { sequence: number; blob: Blob }[] = [];
    const recorder = createBrowserRecorder({
      stream: {} as MediaStream,
      mediaRecorderCtor: FakeMediaRecorder as unknown as typeof MediaRecorder,
      timesliceMs: 2_000,
      stallTimeoutMs: 6_000,
      onStall: (info) => stalls.push(info),
      onChunk: (chunk) => {
        chunks.push(chunk);
      },
    });

    recorder.start(2_000);
    await vi.advanceTimersByTimeAsync(9_000);

    expect(chunks.length).toBeGreaterThanOrEqual(4);
    expect(stalls).toEqual([]);

    await recorder.stop({ emitTail: false });
  });

  it("does not report a stall for a paused recorder", async () => {
    vi.useFakeTimers();

    const stalls: unknown[] = [];
    const recorder = createBrowserRecorder({
      stream: {} as MediaStream,
      mediaRecorderCtor: FakeMediaRecorder as unknown as typeof MediaRecorder,
      timesliceMs: 2_000,
      stallTimeoutMs: 6_000,
      onStall: (info) => stalls.push(info),
      onChunk: () => {},
    });

    recorder.start(2_000);
    await recorder.pause();

    await vi.advanceTimersByTimeAsync(60_000);

    expect(stalls).toEqual([]);

    await recorder.stop({ emitTail: false });
  });
});
