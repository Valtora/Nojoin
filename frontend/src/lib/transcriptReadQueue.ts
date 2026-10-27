import type { TranscriptReadMode } from "./transcriptEvents";

export const TRANSCRIPT_READ_TIMEOUT_MS = 15_000;

/** Drop and abort a stalled read before it can overwrite a later response. */
export async function transcriptReadWithTimeout<Result>(
  read: (signal: AbortSignal) => Promise<Result>,
  signal?: AbortSignal,
): Promise<Result> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let rejectAbort: (() => void) | undefined;
  try {
    return await Promise.race([
      read(controller.signal),
      new Promise<never>((_, reject) => {
        rejectAbort = () => reject(new Error("Transcript read aborted"));
        controller.signal.addEventListener("abort", rejectAbort, { once: true });
        if (controller.signal.aborted) rejectAbort();
      }),
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new Error("Transcript read timed out"));
        }, TRANSCRIPT_READ_TIMEOUT_MS);
      }),
    ]);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
    if (rejectAbort) controller.signal.removeEventListener("abort", rejectAbort);
  }
}

/** Burst hints become one pending read; a full repair always takes priority. */
export function coalesceTranscriptReads(
  read: (mode: TranscriptReadMode) => Promise<unknown>,
  onError: (error: unknown) => void,
): { request: (mode: TranscriptReadMode) => void; close: () => void } {
  let inFlight = false;
  let pending: TranscriptReadMode | null = null;
  let closed = false;
  const request = (mode: TranscriptReadMode): void => {
    if (closed) return;
    if (inFlight) {
      pending = pending === "full" || mode === "full" ? "full" : "delta";
      return;
    }
    inFlight = true;
    void Promise.resolve().then(() => closed ? undefined : read(mode))
      .catch((error) => { if (!closed) onError(error); }).finally(() => {
      inFlight = false;
      if (pending !== null && !closed) {
        const next = pending;
        pending = null;
        request(next);
      }
    });
  };
  return { request, close: () => { closed = true; pending = null; } };
}

/** Includes explicit action refreshes in the same queue as background reads. */
export function serialiseTranscriptReads<Args extends unknown[], Result>(
  read: (...args: Args) => Promise<Result>,
): (...args: Args) => Promise<Result> {
  let tail: Promise<unknown> = Promise.resolve();
  return (...args) => {
    const next = tail.catch(() => undefined).then(() => read(...args));
    tail = next;
    return next;
  };
}
