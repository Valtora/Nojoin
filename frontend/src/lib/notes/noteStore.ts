import { useSessionStore } from "@/lib/session";
import type { RecordingId } from "@/types";

/**
 * The note box's notes not saved yet, and the text still in it, kept in
 * IndexedDB so that a reload, a crashed tab or an accepted leave prompt does
 * not lose them (issue #537). There is one record per account and recording,
 * rewritten whenever the recording's outbox changes and removed once it holds
 * nothing.
 *
 * Records are keyed by the account's id, which a rename does not change. Only
 * the signed-in account's records are kept: the first `/users/me` after a
 * sign-in as another account removes every other account's (except, in a page
 * holding sign-in for its owner, the owner's), and signing out removes them all.
 *
 * Persistence is best-effort, as for capture's segments (`segmentStore`). A
 * browser without IndexedDB, or one that refuses a read or a write, keeps the
 * notes in memory only, which is how the outbox behaved before.
 */

/**
 * The record layout this version writes. A record from a later version is
 * neither read nor overwritten; one from an earlier version is upgraded by
 * `RECORD_MIGRATIONS`, or else left alone the same way. A note of a kind this
 * version can't send is written back as it was found.
 */
export const NOTE_RECORD_VERSION = 1;

export interface StoredDiscard {
  id: string;
  writeToken: string;
}

export interface StoredOutbox {
  version: number;
  /** The account's id (`SessionState.userId`). */
  account: number;
  recordingId: RecordingId;
  /** The notes not saved yet, oldest first; see `readStoredOutbox`. */
  notes: unknown[];
  draft: unknown;
  /** Notes discarded here whose delete the server has not confirmed. */
  discards: StoredDiscard[];
}

export interface NoteStore {
  /** The stored record, unread: anything another version may have written. */
  get(account: number, recordingId: RecordingId): Promise<unknown>;
  put(record: StoredOutbox): Promise<void>;
  remove(account: number, recordingId: RecordingId): Promise<void>;
  /** Removes every record of an account not in `accounts`. */
  keepOnly(accounts: readonly number[]): Promise<void>;
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
      const opening = new Promise<IDBDatabase>((resolve, reject) => {
        const request = this.factory.open(DATABASE_NAME, DATABASE_VERSION);
        request.onupgradeneeded = () => {
          const database = request.result;
          if (!database.objectStoreNames.contains(STORE_NAME)) {
            database.createObjectStore(STORE_NAME, { keyPath: ["account", "recordingId"] });
          }
        };
        request.onsuccess = () => {
          const database = request.result;
          // A later version, opened in another tab, waits for every open
          // connection to close: this one closes, and opens afresh next time.
          database.onversionchange = () => {
            database.close();
            if (this.database === opening) this.database = null;
          };
          resolve(database);
        };
        request.onerror = () => reject(request.error);
      });
      this.database = opening;
      // A failed open is retried on the next call instead of being cached.
      opening.catch(() => {
        if (this.database === opening) this.database = null;
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

  async get(account: number, recordingId: RecordingId) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readonly");
    return requestResult(transaction.objectStore(STORE_NAME).get([account, recordingId]));
  }

  put(record: StoredOutbox) {
    return this.write((store) => store.put(record));
  }

  remove(account: number, recordingId: RecordingId) {
    return this.write((store) => store.delete([account, recordingId]));
  }

  async keepOnly(accounts: readonly number[]) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const keys = await requestResult(store.getAllKeys());
    for (const key of keys) {
      if (Array.isArray(key) && !accounts.includes(key[0] as number)) store.delete(key);
    }
    await transactionDone(transaction);
  }

  clear() {
    return this.write((store) => store.clear());
  }
}

export class MemoryNoteStore implements NoteStore {
  private readonly records = new Map<string, StoredOutbox>();

  private key(account: number, recordingId: RecordingId) {
    return JSON.stringify([account, recordingId]);
  }

  async get(account: number, recordingId: RecordingId) {
    return structuredClone(this.records.get(this.key(account, recordingId)));
  }

  async put(record: StoredOutbox) {
    this.records.set(this.key(record.account, record.recordingId), structuredClone(record));
  }

  async remove(account: number, recordingId: RecordingId) {
    this.records.delete(this.key(account, recordingId));
  }

  async keepOnly(accounts: readonly number[]) {
    for (const [key, record] of this.records) {
      if (!accounts.includes(record.account)) this.records.delete(key);
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
/** Set by signing out, until the server accepts a sign-in again: nothing is stored meanwhile. */
let signedOut = false;
const signedBackIn = new Set<() => void>();

/** The page's store, or null when notes can only be kept in memory, or not at all just now. */
export const sharedNoteStore = (): NoteStore | null => {
  if (signedOut) return null;
  if (shared === undefined) {
    const factory = browserIndexedDb();
    shared = factory ? new IndexedDbNoteStore(factory) : null;
  }
  return shared;
};

/** Calls `listener` when storing resumes after a sign-out that did not leave the page. */
export const subscribeStoringResumed = (listener: () => void): (() => void) => {
  signedBackIn.add(listener);
  return () => signedBackIn.delete(listener);
};

let keptAccounts: string | null = null;

/** Removes every record of an account not in `accounts`, unless that was the last thing done. */
const keepOnlyNotesOf = async (accounts: readonly number[]): Promise<void> => {
  const store = sharedNoteStore();
  const key = [...accounts].sort((left, right) => left - right).join(",");
  if (!store || keptAccounts === key) return;
  keptAccounts = key;
  try {
    await store.keepOnly(accounts);
  } catch {
    // Tried again on the next sign-in.
    keptAccounts = null;
  }
};

/**
 * Called whenever the server accepts this browser's session: storing resumes
 * if a sign-out had stopped it, and once the account's id is known (from
 * `/users/me`), every other account's notes are removed, so they are never
 * shown or sent as this one. A page holding sign-in for its owner keeps the
 * owner's until the owner signs back in.
 */
export const storedNotesSignedIn = async (accountId?: number): Promise<void> => {
  if (signedOut) {
    signedOut = false;
    for (const listener of signedBackIn) listener();
  }
  if (accountId === undefined) return;
  const { heldInPlace, userId } = useSessionStore.getState();
  const owner = heldInPlace && userId !== null && userId !== accountId ? [userId] : [];
  await keepOnlyNotesOf([accountId, ...owner]);
};

/** How long signing out waits for the stored notes to be removed. */
export const CLEAR_ON_SIGN_OUT_MS = 2_000;

/**
 * Signing out: removes every stored note, and stores none until the server
 * accepts a sign-in again (which, should the user stay on the page by
 * cancelling the leave prompt, can happen here).
 */
export const clearStoredNotes = async (): Promise<void> => {
  const store = sharedNoteStore();
  signedOut = true;
  keptAccounts = null;
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
  keptAccounts = null;
};
