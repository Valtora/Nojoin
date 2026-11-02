import type { RecordingId } from "@/types";

/**
 * Segments recorded but not yet acknowledged, kept in IndexedDB.
 *
 * The uploader's queue used to live only in memory, so closing the tab while
 * it was behind lost everything it held. On the meeting that prompted issue
 * #417 that was 14 minutes, sitting behind a "Paused" label that invited
 * exactly that. A segment is written here when it is recorded and removed once
 * the server acknowledges it, so whatever is left after a reload is audio the
 * server never received, and the next resume or stop uploads it first.
 *
 * Persistence is best-effort. A browser without IndexedDB, or one that refuses
 * a write, falls back to the in-memory queue, which is how capture behaved
 * before.
 */

export interface StoredSegment {
  recordingId: RecordingId;
  sequence: number;
  blob: Blob;
  durationSeconds: number;
  /**
   * The capture epoch the segment is queued under. Absent on segments a build
   * from before epochs kept, which count as epoch 0, the epoch such a capture
   * had (issue #570).
   */
  epoch?: number;
  /**
   * The id of the account the capture belongs to, when the page knew it.
   * Absent on segments kept before this was recorded. It lets the browser tell
   * a recording that is gone from one owned by another account, since the
   * server answers 404 for both (issue #589).
   */
  ownerId?: number;
  /**
   * Set on the first segment of new audio after a resume that restored audio
   * recorded before the pause: the line break that resume stored, which this
   * segment moves to its own sequence when it uploads (issue #815). Kept here
   * so a later page that sends it still moves the break.
   */
  resumeMark?: number;
}

export interface SegmentStore {
  put(segment: StoredSegment): Promise<void>;
  remove(recordingId: RecordingId, sequence: number): Promise<void>;
  /** The recording's segments in sequence order. */
  list(recordingId: RecordingId): Promise<StoredSegment[]>;
  clear(recordingId: RecordingId): Promise<void>;
  /** Each recording the store holds any segment for, once. */
  recordingIds(): Promise<RecordingId[]>;
}

const DATABASE_NAME = "nojoin-capture";
const DATABASE_VERSION = 1;
const STORE_NAME = "segments";
const RECORDING_INDEX = "byRecording";

const requestResult = <T>(request: IDBRequest<T>) =>
  new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });

const transactionDone = (transaction: IDBTransaction) =>
  new Promise<void>((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });

const bySequence = (left: StoredSegment, right: StoredSegment) =>
  left.sequence - right.sequence;

export class IndexedDbSegmentStore implements SegmentStore {
  private database: Promise<IDBDatabase> | null = null;

  constructor(private readonly factory: IDBFactory) {}

  private open() {
    if (!this.database) {
      this.database = new Promise<IDBDatabase>((resolve, reject) => {
        const request = this.factory.open(DATABASE_NAME, DATABASE_VERSION);
        request.onupgradeneeded = () => {
          const database = request.result;
          if (!database.objectStoreNames.contains(STORE_NAME)) {
            const store = database.createObjectStore(STORE_NAME, {
              keyPath: ["recordingId", "sequence"],
            });
            store.createIndex(RECORDING_INDEX, "recordingId");
          }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      // A failed open is retried on the next call instead of being cached.
      this.database.catch(() => {
        this.database = null;
      });
    }
    return this.database;
  }

  async put(segment: StoredSegment) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).put(segment);
    await transactionDone(transaction);
  }

  async remove(recordingId: RecordingId, sequence: number) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).delete([recordingId, sequence]);
    await transactionDone(transaction);
  }

  async list(recordingId: RecordingId) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readonly");
    const segments = await requestResult(
      transaction
        .objectStore(STORE_NAME)
        .index(RECORDING_INDEX)
        .getAll(recordingId) as IDBRequest<StoredSegment[]>,
    );
    return segments.sort(bySequence);
  }

  async clear(recordingId: RecordingId) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const keys = await requestResult(
      store.index(RECORDING_INDEX).getAllKeys(recordingId),
    );
    for (const key of keys) {
      store.delete(key);
    }
    await transactionDone(transaction);
  }

  async recordingIds() {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readonly");
    const cursor = transaction
      .objectStore(STORE_NAME)
      .index(RECORDING_INDEX)
      .openKeyCursor(null, "nextunique");
    const ids: RecordingId[] = [];
    await new Promise<void>((resolve, reject) => {
      cursor.onsuccess = () => {
        if (!cursor.result) {
          resolve();
          return;
        }
        ids.push(cursor.result.key as RecordingId);
        cursor.result.continue();
      };
      cursor.onerror = () => reject(cursor.error);
    });
    return ids;
  }
}

export class MemorySegmentStore implements SegmentStore {
  private readonly segments = new Map<string, StoredSegment>();

  private key(recordingId: RecordingId, sequence: number) {
    return `${recordingId}:${sequence}`;
  }

  async put(segment: StoredSegment) {
    this.segments.set(this.key(segment.recordingId, segment.sequence), segment);
  }

  async remove(recordingId: RecordingId, sequence: number) {
    this.segments.delete(this.key(recordingId, sequence));
  }

  async list(recordingId: RecordingId) {
    return [...this.segments.values()]
      .filter((segment) => segment.recordingId === recordingId)
      .sort(bySequence);
  }

  async clear(recordingId: RecordingId) {
    for (const segment of await this.list(recordingId)) {
      this.segments.delete(this.key(recordingId, segment.sequence));
    }
  }

  async recordingIds() {
    return [...new Set([...this.segments.values()].map((segment) => segment.recordingId))];
  }
}

export const createSegmentStore = (
  factory: IDBFactory | undefined = typeof indexedDB === "undefined" ? undefined : indexedDB,
): SegmentStore => (factory ? new IndexedDbSegmentStore(factory) : new MemorySegmentStore());

/** The capture epoch a stored segment is queued under. */
export const storedSegmentEpoch = (segment: StoredSegment) => segment.epoch ?? 0;

/**
 * The run of stored segments that continues the server's own.
 *
 * A segment after a gap could not be finalized without the one missing before
 * it, so anything past the first gap is dropped rather than uploaded.
 */
export const contiguousSegmentsAfter = (
  segments: StoredSegment[],
  serverLastSequence: number,
): StoredSegment[] => {
  const run: StoredSegment[] = [];
  let expected = serverLastSequence + 1;
  for (const segment of segments) {
    if (segment.sequence < expected) {
      continue;
    }
    if (segment.sequence !== expected) {
      break;
    }
    run.push(segment);
    expected += 1;
  }
  return run;
};
