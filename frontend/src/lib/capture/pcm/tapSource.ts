/**
 * The page side of the PCM tap worklet (public/audio-worklets/pcm-tap.js).
 *
 * The worklet transfers blocks of raw audio from the rendering thread; this
 * fans them out to subscribers and turns the worklet's flush acknowledgement
 * into a promise, so a pause or stop can collect every frame rendered before
 * it rather than losing the partial block still sitting in the worklet.
 */

export const PCM_TAP_PROCESSOR_NAME = "nojoin-pcm-tap";

export const PCM_TAP_MODULE_URL = "/audio-worklets/pcm-tap.js";

/** About 85 ms at 48 kHz: small enough for a prompt stall signal, large enough to keep messages rare. */
export const PCM_TAP_BLOCK_FRAMES = 4096;

const DEFAULT_FLUSH_TIMEOUT_MS = 1_000;

export type PcmBlockListener = (block: Float32Array[]) => void;

export interface PcmSource {
  readonly sampleRate: number;
  readonly channelCount: number;
  subscribe(listener: PcmBlockListener): () => void;
  /**
   * Resolves true once every frame rendered so far has been delivered, or false
   * if the audio thread did not answer in time (a suspended context renders
   * nothing, so there is nothing further to wait for).
   */
  flush(timeoutMs?: number): Promise<boolean>;
  close(): void;
}

export interface PcmTapPort {
  postMessage(message: unknown): void;
  onmessage: ((event: MessageEvent) => void) | null;
}

interface TapMessage {
  type?: string;
  id?: number;
  channels?: Float32Array[];
}

export const createPortPcmSource = (
  port: PcmTapPort,
  sampleRate: number,
  channelCount: number,
): PcmSource => {
  const listeners = new Set<PcmBlockListener>();
  const pendingFlushes = new Map<number, (delivered: boolean) => void>();
  let nextFlushId = 1;
  let closed = false;

  port.onmessage = (event: MessageEvent) => {
    const message = (event.data ?? {}) as TapMessage;
    if (message.type === "pcm" && message.channels) {
      const channels = message.channels;
      listeners.forEach((listener) => listener(channels));
    } else if (message.type === "flushed" && typeof message.id === "number") {
      const resolve = pendingFlushes.get(message.id);
      pendingFlushes.delete(message.id);
      resolve?.(true);
    }
  };

  return {
    sampleRate,
    channelCount,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    flush(timeoutMs = DEFAULT_FLUSH_TIMEOUT_MS) {
      if (closed) {
        return Promise.resolve(false);
      }
      const id = nextFlushId;
      nextFlushId += 1;
      return new Promise<boolean>((resolve) => {
        const timer = setTimeout(() => {
          pendingFlushes.delete(id);
          resolve(false);
        }, timeoutMs);
        pendingFlushes.set(id, (delivered) => {
          clearTimeout(timer);
          resolve(delivered);
        });
        port.postMessage({ type: "flush", id });
      });
    },
    close() {
      if (closed) {
        return;
      }
      closed = true;
      port.postMessage({ type: "stop" });
      pendingFlushes.forEach((resolve) => resolve(false));
      pendingFlushes.clear();
      listeners.clear();
      port.onmessage = null;
    },
  };
};
