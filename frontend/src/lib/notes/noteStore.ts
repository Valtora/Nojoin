import type { RecordingId } from "@/types";

/**
 * The note box's notes not saved yet, and the text still in it, kept in
 * IndexedDB so that a reload, a crashed tab or an accepted leave prompt does
 * not lose them (issue #537). There is one record per account and recording,
 * rewritten whenever the recording's outbox changes and removed once it holds
 * nothing.
 *
 * Only the signed-in account's records are kept: signing in as another
 * account removes every other account's, and signing out removes them all.
 *
 * Persistence is best-effort, as for capture's segments (`segmentStore`). A
 * browser without IndexedDB, or one that refuses a read or a write, keeps the
 * notes in memory only, which is how the outbox behaved before.
 */

/**
 * The record layout this version writes. A record from a later version is
 * neither read nor overwritten, and a note of a kind this version can't send
 * is written back as it was found.
 */
export const NOTE_RECORD_VERSION = 1;

export interface StoredDiscard {
  id: string;
  writeToken: string;
}

export interface StoredOutbox {
  version: number;
  account: string;
  recordingId: RecordingId;
  /** The notes not saved yet, oldest first; see `readStoredOutbox`. */
  notes: unknown[];
  draft: unknown;
  /** Notes discarded here whose delete the server has not confirmed. */
  discards: StoredDiscard[];
}

export interface NoteStore {
  /** The stored record, unread: anything a later version may have written. */
  get(account: string, recordingId: RecordingId): Promise<unknown>;
  put(record: StoredOutbox): Promise<void>;
  remove(account: string, recordingId: RecordingId): Promise<void>;
  /** Removes every other account's records. */
  keepOnly(account: string): Promise<void>;
  clear(): Promise<void>;
}

const DATABASE_NAME = "nojoin-notes";
const DATABASE_VERSION = 1;
const STORE_NAME = "outboxes";

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

export class IndexedDbNoteStore implements NoteStore {
  private database: Promise<IDBDatabase> | null = null;

  constructor(private readonly factory: IDBFactory) {}

  private open() {
    if (!this.database) {
      this.database = new Promise<IDBDatabase>((resolve, reject) => {
        const request = this.factory.open(DATABASE_NAME, DATABASE_VERSION);
        request.onupgradeneeded = () => {
          const database = request.result;
          if (!database.objectStoreNames.contains(STORE_NAME)) {
            database.createObjectStore(STORE_NAME, { keyPath: ["account", "recordingId"] });
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

  private async write(change: (store: IDBObjectStore) => void) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readwrite");
    change(transaction.objectStore(STORE_NAME));
    await transactionDone(transaction);
  }

  async get(account: string, recordingId: RecordingId) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readonly");
    return requestResult(transaction.objectStore(STORE_NAME).get([account, recordingId]));
  }

  put(record: StoredOutbox) {
    return this.write((store) => store.put(record));
  }

  remove(account: string, recordingId: RecordingId) {
    return this.write((store) => store.delete([account, recordingId]));
  }

  async keepOnly(account: string) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const keys = await requestResult(store.getAllKeys());
    for (const key of keys) {
      if (Array.isArray(key) && key[0] !== account) store.delete(key);
    }
    await transactionDone(transaction);
  }

  clear() {
    return this.write((store) => store.clear());
  }
}

export class MemoryNoteStore implements NoteStore {
  private readonly records = new Map<string, StoredOutbox>();

  private key(account: string, recordingId: RecordingId) {
    return JSON.stringify([account, recordingId]);
  }

  async get(account: string, recordingId: RecordingId) {
    return structuredClone(this.records.get(this.key(account, recordingId)));
  }

  async put(record: StoredOutbox) {
    this.records.set(this.key(record.account, record.recordingId), structuredClone(record));
  }

  async remove(account: string, recordingId: RecordingId) {
    this.records.delete(this.key(account, recordingId));
  }

  async keepOnly(account: string) {
    for (const [key, record] of this.records) {
      if (record.account !== account) this.records.delete(key);
    }
  }

  async clear() {
    this.records.clear();
  }
}

/** The browser's IndexedDB, or null where there is none or it may not be used. */
const browserIndexedDb = (): IDBFactory | null => {
  try {
    return globalThis.indexedDB ?? null;
  } catch {
    // Reading the global throws where site data is blocked.
    return null;
  }
};

let shared: NoteStore | null | undefined;
/** Set once this page has signed out: nothing is stored from it again. */
let signedOut = false;

/** The page's store, or null when notes can only be kept in memory. */
export const sharedNoteStore = (): NoteStore | null => {
  if (signedOut) return null;
  if (shared === undefined) {
    const factory = browserIndexedDb();
    shared = factory ? new IndexedDbNoteStore(factory) : null;
  }
  return shared;
};

let keptAccount: string | null = null;

/**
 * Removes every account's stored notes but `account`'s. Called whenever the
 * server says who the browser is signed in as, so another account's notes are
 * gone before any page of this one can open them.
 */
export const keepOnlyNotesOf = async (account: string): Promise<void> => {
  const store = sharedNoteStore();
  if (!store || keptAccount === account) return;
  keptAccount = account;
  try {
    await store.keepOnly(account);
  } catch {
    // Tried again on the next sign-in.
    keptAccount = null;
  }
};

/** How long signing out waits for the stored notes to be removed. */
export const CLEAR_ON_SIGN_OUT_MS = 2_000;

/** Signing out: removes every stored note, and stores none from this page again. */
export const clearStoredNotes = async (): Promise<void> => {
  const store = sharedNoteStore();
  signedOut = true;
  if (!store) return;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    // A storage that never answers must not hold up leaving for the sign-in page.
    await Promise.race([
      store.clear(),
      new Promise<void>((resolve) => {
        timer = setTimeout(resolve, CLEAR_ON_SIGN_OUT_MS);
      }),
    ]);
  } catch {
    // Nothing more to do: the site's data can still be cleared by hand.
  } finally {
    clearTimeout(timer);
  }
};

/**
 * Tests: use `store` as the page's store (with none, look for the browser's
 * again), and forget any sign-out.
 */
export const setNoteStoreForTests = (store?: NoteStore | null): void => {
  shared = store;
  signedOut = false;
  keptAccount = null;
};
