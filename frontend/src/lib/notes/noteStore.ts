import { isRecord } from "@/lib/errors";
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
 * the signed-in account's records are kept: each `/users/me` removes every
 * other account's, and signing out removes them all, telling the other tabs
 * where the browser can (`StoreEvent`).
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
  /** The recordings `account` has a record for. */
  recordingsOf(account: number): Promise<RecordingId[]>;
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

  async recordingsOf(account: number) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readonly");
    // Every key, filtered here: each sign-in leaves one account's records.
    const keys = await requestResult(transaction.objectStore(STORE_NAME).getAllKeys());
    return keys.flatMap((key) =>
      Array.isArray(key) && key[0] === account && typeof key[1] === "string"
        ? [key[1] as RecordingId]
        : [],
    );
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

  async recordingsOf(account: number) {
    return [...this.records.values()]
      .filter((record) => record.account === account)
      .map((record) => record.recordingId);
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
/**
 * Set by signing out, in this tab or another, until the server accepts a
 * sign-in in this tab again: nothing is stored meanwhile.
 */
let signedOut = false;

/** A change to the page's store, made in this tab or told by another. */
export type StoreEvent =
  /** Signing out stopped storing. */
  | { kind: "stopped" }
  /** The next accepted sign-in resumed it. */
  | { kind: "resumed" }
  /** Every record of an account not in `accounts` was removed. */
  | { kind: "kept"; accounts: number[] };

const storeListeners = new Set<(event: StoreEvent) => void>();

const emit = (event: StoreEvent) => {
  for (const listener of storeListeners) listener(event);
};

/**
 * Tabs share the store, so each tells the others what it removed: a sign-out
 * stops every tab storing, and the cleanup after a sign-in stops a tab storing
 * for an account it removed, or lets a tab of the account it kept store again.
 * Where the browser has no BroadcastChannel, a tab finds out only from its own
 * session.
 */
const CHANNEL_NAME = "nojoin-notes";

type Ping = { kind: "signed-out" } | { kind: "kept"; accounts: number[] };

let channel: BroadcastChannel | null | undefined;

const isPing = (data: unknown): data is Ping =>
  isRecord(data) &&
  (data.kind === "signed-out" ||
    (data.kind === "kept" &&
      Array.isArray(data.accounts) &&
      data.accounts.every((account) => Number.isInteger(account))));

const stopStoring = () => {
  if (signedOut) return;
  signedOut = true;
  emit({ kind: "stopped" });
};

const resumeStoring = () => {
  if (!signedOut) return;
  signedOut = false;
  emit({ kind: "resumed" });
};

/**
 * Repeats another tab's removal in this tab. A write of this tab's can land
 * between that tab's removal and its ping; this removal comes after it, and
 * after any write this tab had begun. A tab that never opened the store has
 * written nothing.
 */
const removeAgain = (remove: (store: NoteStore) => Promise<void>) => {
  if (!shared) return;
  // Refused: the next sign-in or sign-out removes them again.
  remove(shared).catch(() => undefined);
};

const heard = (data: unknown) => {
  if (!isPing(data)) return;
  if (data.kind === "signed-out") {
    stopStoring();
    removeAgain((store) => store.clear());
    return;
  }
  const { userId } = useSessionStore.getState();
  if (userId !== null) {
    // Another tab's sign-in as this tab's account: the browser is that
    // account's again, so this tab may store its notes again. As another
    // account, it removed this account's notes, and so does this tab.
    if (data.accounts.includes(userId)) resumeStoring();
    else removeAgain((store) => store.keepOnly(data.accounts));
  }
  emit({ kind: "kept", accounts: data.accounts });
};

const tabsChannel = (): BroadcastChannel | null => {
  if (channel === undefined) {
    try {
      channel = typeof BroadcastChannel === "function" ? new BroadcastChannel(CHANNEL_NAME) : null;
    } catch {
      // Refused where site data is blocked: this tab goes by its own session.
      channel = null;
    }
    if (channel) channel.onmessage = (event: MessageEvent) => heard(event.data);
  }
  return channel;
};

const tell = (ping: Ping) => {
  try {
    tabsChannel()?.postMessage(ping);
  } catch {
    // A closed channel: the other tabs go by their own sessions.
  }
};

/** The page's store, or null when notes can only be kept in memory, or not at all just now. */
export const sharedNoteStore = (): NoteStore | null => {
  if (signedOut) return null;
  if (shared === undefined) {
    const factory = browserIndexedDb();
    shared = factory ? new IndexedDbNoteStore(factory) : null;
  }
  return shared;
};

/** Whether a sign-out, in this tab or another, has stopped storing until the next accepted sign-in. */
export const storingStopped = (): boolean => signedOut;

/** Calls `listener` on each change to the store, made in this tab or another. */
export const subscribeStoreEvents = (listener: (event: StoreEvent) => void): (() => void) => {
  tabsChannel();
  storeListeners.add(listener);
  return () => storeListeners.delete(listener);
};

/**
 * Called whenever the server accepts this tab's session: storing resumes if a
 * sign-out had stopped it, and once the account's id is known (from
 * `/users/me`), every other account's notes are removed, so they are never
 * shown or sent as this one. Other tabs are told, so a page writing for a
 * removed account stops until that account signs back in on it.
 */
export const storedNotesSignedIn = async (accountId?: number): Promise<void> => {
  resumeStoring();
  if (accountId === undefined) return;
  const store = sharedNoteStore();
  if (!store) return;
  const accounts = [accountId];
  try {
    await store.keepOnly(accounts);
  } catch {
    // Tried again on the next sign-in.
    return;
  }
  emit({ kind: "kept", accounts });
  tell({ kind: "kept", accounts });
};

/** How long signing out waits for the stored notes to be removed. */
export const CLEAR_ON_SIGN_OUT_MS = 2_000;

/**
 * Signing out: removes every stored note, and stops this tab and every other
 * storing any until the server accepts a sign-in in it again (which, should
 * the user stay on the page by cancelling the leave prompt, can happen there),
 * or another tab signs in as its account.
 */
export const clearStoredNotes = async (): Promise<void> => {
  const store = sharedNoteStore();
  stopStoring();
  tell({ kind: "signed-out" });
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
 * again), forget any sign-out, and close the channel to other tabs.
 */
export const setNoteStoreForTests = (store?: NoteStore | null): void => {
  shared = store;
  signedOut = false;
  channel?.close();
  channel = undefined;
};
