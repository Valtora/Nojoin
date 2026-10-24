import { uploadCapture, type Capture } from "@/lib/api";
import { getErrorMessage, getErrorStatus } from "@/lib/errors";
import { holdPendingNoteWrite } from "@/lib/notes/noteOutboxes";
import type { RecordingId } from "@/types";

import { previewDataUrl } from "./frameImage";

/**
 * Captures on their way to the server, one list per recording for the life of
 * the page, so a capture taken in the live view still uploads, or waits to be
 * retried, after the view is gone.
 *
 * A capture uploads at once, while the recording goes on: it is a document
 * beside the audio, not part of it. A failed upload is tried twice more, then
 * waits with Retry and Discard. While anything here is unsent the page asks
 * before it closes and holds sign-in in place, as an unsent note does
 * (`holdPendingNoteWrite`). The image is held in memory only: a closed tab
 * loses a capture that never reached the server, which the leave warning
 * says before it happens.
 */

export type PendingCaptureStatus = "sending" | "failed";

export interface PendingCapture {
  key: string;
  recordingId: RecordingId;
  captureMs: number;
  /** A small copy to show while it uploads; null until drawn, or if it cannot be. */
  preview: string | null;
  status: PendingCaptureStatus;
  error: string | null;
}

interface Entry extends PendingCapture {
  image: Blob;
  release: () => void;
}

type Upload = (recordingId: RecordingId, image: Blob, captureMs: number) => Promise<Capture>;
type Sleep = (ms: number) => Promise<void>;

/** The pauses before the second and third attempts. */
export const CAPTURE_RETRY_DELAYS_MS = [1_000, 3_000] as const;

const NO_PENDING: readonly PendingCapture[] = Object.freeze([]);

/** Refusals that another attempt cannot change: a bad image or a gone recording. */
const isFinal = (error: unknown) => {
  const status = getErrorStatus(error);
  return status !== null && status >= 400 && status < 500 && status !== 408 && status !== 429;
};

let sequence = 0;

export class CaptureOutbox {
  private readonly lists = new Map<RecordingId, Entry[]>();
  private readonly snapshots = new Map<RecordingId, readonly PendingCapture[]>();
  private readonly listeners = new Set<() => void>();
  private readonly uploadedListeners = new Set<(capture: Capture) => void>();

  constructor(
    private readonly upload: Upload = uploadCapture,
    private readonly sleep: Sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    private readonly drawPreview: (image: Blob) => Promise<string | null> = previewDataUrl,
  ) {}

  /** Queues a capture and starts sending it. Returns its key. */
  add(recordingId: RecordingId, image: Blob, captureMs: number): string {
    sequence += 1;
    const entry: Entry = {
      key: `capture-${Date.now()}-${sequence}`,
      recordingId,
      captureMs: Math.max(0, Math.round(captureMs)),
      preview: null,
      status: "sending",
      error: null,
      image,
      release: holdPendingNoteWrite(),
    };
    this.lists.set(recordingId, [...(this.lists.get(recordingId) ?? []), entry]);
    this.changed(recordingId);
    void this.drawPreview(image).then((preview) => {
      if (preview) this.update(entry.key, { preview });
    });
    void this.send(entry.key);
    return entry.key;
  }

  retry = (key: string) => {
    const entry = this.find(key);
    if (!entry || entry.status !== "failed") return;
    this.update(key, { status: "sending", error: null });
    void this.send(key);
  };

  discard = (key: string) => {
    const entry = this.find(key);
    if (entry) this.remove(entry);
  };

  pending(recordingId: RecordingId): readonly PendingCapture[] {
    return this.snapshots.get(recordingId) ?? NO_PENDING;
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** Calls `listener` with each capture the server has stored. */
  onUploaded(listener: (capture: Capture) => void) {
    this.uploadedListeners.add(listener);
    return () => {
      this.uploadedListeners.delete(listener);
    };
  }

  private async send(key: string) {
    for (let attempt = 0; ; attempt += 1) {
      const entry = this.find(key);
      if (!entry) return;
      try {
        const capture = await this.upload(entry.recordingId, entry.image, entry.captureMs);
        const stillHere = this.find(key);
        if (stillHere) this.remove(stillHere);
        for (const listener of this.uploadedListeners) listener(capture);
        return;
      } catch (error: unknown) {
        if (!this.find(key)) return;
        const delay = CAPTURE_RETRY_DELAYS_MS[attempt];
        if (isFinal(error) || delay === undefined) {
          this.update(key, {
            status: "failed",
            error: getErrorMessage(error, "The capture could not be saved."),
          });
          return;
        }
        await this.sleep(delay);
      }
    }
  }

  private find(key: string): Entry | undefined {
    for (const list of this.lists.values()) {
      const entry = list.find((item) => item.key === key);
      if (entry) return entry;
    }
    return undefined;
  }

  private update(key: string, change: Partial<PendingCapture>) {
    const entry = this.find(key);
    if (!entry) return;
    const list = this.lists.get(entry.recordingId) ?? [];
    this.lists.set(
      entry.recordingId,
      list.map((item) => (item.key === key ? { ...item, ...change } : item)),
    );
    this.changed(entry.recordingId);
  }

  private remove(entry: Entry) {
    entry.release();
    const list = (this.lists.get(entry.recordingId) ?? []).filter(
      (item) => item.key !== entry.key,
    );
    if (list.length) this.lists.set(entry.recordingId, list);
    else this.lists.delete(entry.recordingId);
    this.changed(entry.recordingId);
  }

  private changed(recordingId: RecordingId) {
    const list = this.lists.get(recordingId);
    if (list?.length) {
      this.snapshots.set(
        recordingId,
        list.map(({ key, recordingId: id, captureMs, preview, status, error }) => ({
          key,
          recordingId: id,
          captureMs,
          preview,
          status,
          error,
        })),
      );
    } else {
      this.snapshots.delete(recordingId);
    }
    for (const listener of this.listeners) listener();
  }
}

/** The page's outbox. */
export const captureOutbox = new CaptureOutbox();
