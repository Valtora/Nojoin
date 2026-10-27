import type { Recording, RecordingSpeaker, TranscriptSegment } from "@/types";

export interface TranscriptSyncResult {
  revision: number;
  segments: TranscriptSegment[];
  speakers: RecordingSpeaker[];
}

export interface DetailTranscriptRead {
  recording: Recording;
  mode: "full" | "delta";
  options: { deferActiveEdit?: boolean };
}

interface PendingRead {
  request: DetailTranscriptRead;
  resolve: ((result: TranscriptSyncResult | null) => void)[];
  reject: ((error: unknown) => void)[];
}

/** One detail read at a time; requests while busy share one following read. */
export function createDetailTranscriptReader(
  read: (request: DetailTranscriptRead, isCurrent: () => boolean) => Promise<TranscriptSyncResult | null>,
) {
  let cancelled = false;
  let running = false;
  let pending: PendingRead | null = null;

  const drain = async (batch: PendingRead) => {
    running = true;
    try {
      const result = await read(batch.request, () => !cancelled);
      batch.resolve.forEach((resolve) => resolve(cancelled ? null : result));
    } catch (error) {
      batch.reject.forEach((reject) => reject(error));
    } finally {
      running = false;
      const next = pending;
      pending = null;
      if (next && !cancelled) void drain(next);
    }
  };

  return {
    request(request: DetailTranscriptRead): Promise<TranscriptSyncResult | null> {
      if (cancelled) return Promise.resolve(null);
      return new Promise((resolve, reject) => {
        if (!running) {
          void drain({ request, resolve: [resolve], reject: [reject] });
          return;
        }
        if (!pending) {
          pending = { request, resolve: [resolve], reject: [reject] };
          return;
        }
        pending.request = {
          recording: request.recording,
          mode: pending.request.mode === "full" || request.mode === "full" ? "full" : "delta",
          options: {
            deferActiveEdit: pending.request.options.deferActiveEdit !== false && request.options.deferActiveEdit !== false,
          },
        };
        pending.resolve.push(resolve);
        pending.reject.push(reject);
      });
    },
    cancel() {
      cancelled = true;
      pending?.resolve.forEach((resolve) => resolve(null));
      pending = null;
    },
  };
}
