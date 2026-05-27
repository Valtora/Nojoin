export interface RecordedChunk {
  sequence: number;
  blob: Blob;
}

export interface CreateBrowserRecorderOptions {
  stream: MediaStream;
  startSequence?: number;
  onChunk: (chunk: RecordedChunk) => void | Promise<void>;
  onError?: (error: Error) => void;
  mediaRecorderCtor?: typeof MediaRecorder;
  timesliceMs?: number;
}

const DEFAULT_MIME_TYPE = "audio/webm;codecs=opus";
const DEFAULT_AUDIO_BITS_PER_SECOND = 160_000;

const resolveRecorderMimeType = (mediaRecorderCtor: typeof MediaRecorder) => {
  if (mediaRecorderCtor.isTypeSupported(DEFAULT_MIME_TYPE)) {
    return DEFAULT_MIME_TYPE;
  }

  if (mediaRecorderCtor.isTypeSupported("audio/webm")) {
    return "audio/webm";
  }

  return "";
};

interface ActiveSegment {
  recorder: MediaRecorder;
  chunks: Blob[];
  timerId: ReturnType<typeof setTimeout> | null;
  stopping: boolean;
  stopPromise: Promise<Blob | null>;
  resolveStop: (blob: Blob | null) => void;
}

export class BrowserRecorder {
  private readonly stream: MediaStream;

  private readonly mediaRecorderCtor: typeof MediaRecorder;

  private readonly mimeType: string;

  private nextSequence: number;

  private onChunk: CreateBrowserRecorderOptions["onChunk"];

  private onError?: CreateBrowserRecorderOptions["onError"];

  private timesliceMs: number;

  private activeSegment: ActiveSegment | null = null;

  private stateValue: RecordingState = "inactive";

  private operationQueue = Promise.resolve();

  private stopPromise: Promise<void> | null = null;

  constructor(options: CreateBrowserRecorderOptions) {
    this.mediaRecorderCtor = options.mediaRecorderCtor ?? MediaRecorder;
    this.mimeType = resolveRecorderMimeType(this.mediaRecorderCtor);
    this.stream = options.stream;
    this.nextSequence = options.startSequence ?? 0;
    this.onChunk = options.onChunk;
    this.onError = options.onError;
    this.timesliceMs = options.timesliceMs ?? 2_000;
  }

  get state() {
    return this.stateValue;
  }

  start(timesliceMs = 2_000) {
    if (this.stateValue === "inactive") {
      this.timesliceMs = timesliceMs;
      this.stateValue = "recording";
      this.beginSegment();
    }
  }

  async requestData() {
    const recorder = this.activeSegment?.recorder;
    if (!recorder || recorder.state !== "recording") {
      return;
    }

    recorder.requestData();
  }

  async pause() {
    if (this.stateValue !== "recording") {
      return;
    }

    await this.enqueueOperation(async () => {
      if (this.stateValue !== "recording") {
        return;
      }

      this.stateValue = "paused";
      const blob = await this.stopActiveSegment();
      this.emitChunk(blob);
    });
  }

  resume() {
    if (this.stateValue === "paused") {
      void this.enqueueOperation(async () => {
        if (this.stateValue !== "paused") {
          return;
        }

        this.stateValue = "recording";
        this.beginSegment();
      });
    }
  }

  stop(options: { emitTail?: boolean } = {}) {
    if (this.stateValue === "inactive" && !this.activeSegment) {
      return Promise.resolve();
    }

    if (!this.stopPromise) {
      const emitTail = options.emitTail !== false;
      this.stopPromise = this.enqueueOperation(async () => {
        this.stateValue = "inactive";
        const blob = await this.stopActiveSegment();
        if (emitTail) {
          this.emitChunk(blob);
        }
      }).finally(() => {
        this.stopPromise = null;
      });
    }

    return this.stopPromise;
  }

  private beginSegment() {
    if (this.stateValue !== "recording" || this.activeSegment) {
      return;
    }

    const recorder = new this.mediaRecorderCtor(this.stream, {
      mimeType: this.mimeType || undefined,
      audioBitsPerSecond: DEFAULT_AUDIO_BITS_PER_SECOND,
    });

    const chunks: Blob[] = [];
    let resolveStop: (blob: Blob | null) => void = () => {};
    const segment: ActiveSegment = {
      recorder,
      chunks,
      timerId: null,
      stopping: false,
      stopPromise: new Promise<Blob | null>((resolve) => {
        resolveStop = resolve;
      }),
      resolveStop,
    };

    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data.size > 0) {
        chunks.push(event.data);
      }
    };

    recorder.onerror = () => {
      this.onError?.(new Error("The browser recorder reported an unexpected error."));
    };

    recorder.onstop = () => {
      if (segment.timerId) {
        clearTimeout(segment.timerId);
        segment.timerId = null;
      }

      const type = recorder.mimeType || this.mimeType || DEFAULT_MIME_TYPE;
      segment.resolveStop(
        chunks.length > 0 ? new Blob(chunks, { type }) : null,
      );
    };

    this.activeSegment = segment;
    recorder.start();
    segment.timerId = setTimeout(() => {
      void this.rollSegment();
    }, this.timesliceMs);
  }

  private async rollSegment() {
    await this.enqueueOperation(async () => {
      if (this.stateValue !== "recording") {
        return;
      }

      const blob = await this.stopActiveSegment();
      this.emitChunk(blob);

      if (this.stateValue === "recording") {
        this.beginSegment();
      }
    });
  }

  private async stopActiveSegment() {
    const segment = this.activeSegment;
    if (!segment) {
      return null;
    }

    this.activeSegment = null;
    if (segment.timerId) {
      clearTimeout(segment.timerId);
      segment.timerId = null;
    }

    if (!segment.stopping) {
      segment.stopping = true;
      if (segment.recorder.state !== "inactive") {
        segment.recorder.stop();
      } else {
        segment.resolveStop(null);
      }
    }

    return segment.stopPromise;
  }

  private emitChunk(blob: Blob | null) {
    if (!blob || blob.size <= 0) {
      return;
    }

    const sequence = this.nextSequence;
    this.nextSequence += 1;
    void Promise.resolve(this.onChunk({ sequence, blob })).catch((error) => {
      this.onError?.(
        error instanceof Error
          ? error
          : new Error("The browser recorder failed to queue a segment."),
      );
    });
  }

  private enqueueOperation<T>(operation: () => Promise<T>) {
    const nextOperation = this.operationQueue.then(operation, operation);
    this.operationQueue = nextOperation.then(
      () => undefined,
      () => undefined,
    );
    return nextOperation;
  }
}

export const createBrowserRecorder = (options: CreateBrowserRecorderOptions) =>
  new BrowserRecorder(options);