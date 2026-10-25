/**
 * Reads frames from a clone of the shared screen's video track in a worker
 * (`public/workers/speaker-frames.js`) for the screen study (#653).
 *
 * The clone is the study's own: stopping it, as `stop` does, leaves the
 * original track and so the share and the recording running. A caller must
 * never pass the original.
 */

export const SPEAKER_FRAMES_WORKER_URL = "/workers/speaker-frames.js";

/** How long the worker may take to answer before the study gives up. */
const REQUEST_TIMEOUT_MS = 10_000;

export interface FrameSample {
  grid: Uint8Array;
  width: number;
  height: number;
}

export interface FrameGrabber {
  /** The newest frame's grid, drawn and held for `encode`; null before the first frame. */
  sample(): Promise<FrameSample | null>;
  /** The frame `sample` last drew, as a JPEG. */
  encode(): Promise<Blob>;
  /** How many frames the worker has received, for diagnostics. */
  framesSeen(): Promise<number>;
  stop(): void;
}

interface TrackProcessor {
  readable: ReadableStream<unknown>;
}

type TrackProcessorConstructor = new (init: { track: MediaStreamTrack }) => TrackProcessor;

interface WorkerLike {
  postMessage(message: unknown, transfer?: Transferable[]): void;
  terminate(): void;
  onmessage: ((event: MessageEvent) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
}

export interface FrameGrabberOptions {
  createWorker?: () => WorkerLike;
  /** The page's MediaStreamTrackProcessor, where it has one. */
  trackProcessor?: TrackProcessorConstructor | null;
}

interface WorkerReply {
  type?: string;
  id?: number;
  empty?: boolean;
  grid?: Uint8Array;
  width?: number;
  height?: number;
  blob?: Blob;
  framesSeen?: number;
  message?: string;
}

const pageTrackProcessor = (): TrackProcessorConstructor | null => {
  const candidate = (globalThis as { MediaStreamTrackProcessor?: unknown })
    .MediaStreamTrackProcessor;
  return typeof candidate === "function" ? (candidate as TrackProcessorConstructor) : null;
};

export const createWorkerFrameGrabber = (
  clone: MediaStreamTrack,
  options: FrameGrabberOptions = {},
): FrameGrabber => {
  const worker: WorkerLike = options.createWorker
    ? options.createWorker()
    : new Worker(SPEAKER_FRAMES_WORKER_URL);
  const pending = new Map<
    number,
    { resolve: (reply: WorkerReply) => void; reject: (error: Error) => void }
  >();
  let nextId = 1;
  let failure: Error | null = null;
  let stopped = false;

  const failAll = (error: Error) => {
    failure = failure ?? error;
    pending.forEach(({ reject }) => reject(error));
    pending.clear();
  };

  worker.onmessage = (event: MessageEvent) => {
    const reply = (event.data ?? {}) as WorkerReply;
    if (reply.type === "error") {
      const error = new Error(reply.message || "The screen study's frame reader failed.");
      if (typeof reply.id === "number" && pending.has(reply.id)) {
        pending.get(reply.id)?.reject(error);
        pending.delete(reply.id);
      }
      failAll(error);
      return;
    }
    if (typeof reply.id !== "number") {
      return;
    }
    const waiter = pending.get(reply.id);
    pending.delete(reply.id);
    waiter?.resolve(reply);
  };
  worker.onerror = () => {
    failAll(new Error("The screen study's frame reader could not run."));
  };

  const request = (type: string): Promise<WorkerReply> => {
    if (failure) {
      return Promise.reject(failure);
    }
    if (stopped) {
      return Promise.reject(new Error("The screen study's frame reader has stopped."));
    }
    const id = nextId;
    nextId += 1;
    return new Promise<WorkerReply>((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error("The screen study's frame reader stopped answering."));
      }, REQUEST_TIMEOUT_MS);
      pending.set(id, {
        resolve: (reply) => {
          clearTimeout(timer);
          resolve(reply);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });
      worker.postMessage({ type, id });
    });
  };

  const Processor =
    options.trackProcessor === undefined ? pageTrackProcessor() : options.trackProcessor;
  try {
    if (Processor) {
      const readable = new Processor({ track: clone }).readable;
      worker.postMessage({ type: "start", readable }, [readable as unknown as Transferable]);
    } else {
      worker.postMessage({ type: "start-track", track: clone }, [
        clone as unknown as Transferable,
      ]);
    }
  } catch (error: unknown) {
    worker.terminate();
    throw error instanceof Error
      ? error
      : new Error("This browser cannot read the shared screen in the background.");
  }

  return {
    async sample() {
      const reply = await request("sample");
      if (reply.empty || !reply.grid || !reply.width || !reply.height) {
        return null;
      }
      return { grid: reply.grid, width: reply.width, height: reply.height };
    },
    async encode() {
      const reply = await request("encode");
      if (!(reply.blob instanceof Blob)) {
        throw new Error("The screen study's frame reader returned no image.");
      }
      return reply.blob;
    },
    async framesSeen() {
      const reply = await request("stats");
      return reply.framesSeen ?? 0;
    },
    stop() {
      if (stopped) {
        return;
      }
      stopped = true;
      failAll(new Error("The screen study's frame reader has stopped."));
      try {
        worker.postMessage({ type: "stop" });
      } catch {
        // Already gone.
      }
      worker.terminate();
      clone.stop();
    },
  };
};
