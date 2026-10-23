import { useNotificationStore } from "@/lib/notificationStore";
import type { RecordingId } from "@/types";

/**
 * Audio this browser recorded that can never join its recording, kept as one
 * FLAC file so the user can download it (issue #605).
 *
 * A tab's unsent audio is left out of the recording when the capture is
 * resumed or stopped in another tab or on another device first, since it would
 * land out of order or after the end. It was once removed outright, losing a
 * stretch of the meeting only that browser heard. It is now joined into one
 * file, kept for `LEFT_OUT_AUDIO_KEEP_MS`, and offered to the account it was
 * recorded under until that account downloads or deletes it.
 *
 * The audio sits in a database of its own rather than beside the capture
 * queue in `nojoin-capture`: adding a store there would need a version
 * upgrade, which a tab still open on an older build blocks.
 */

export interface LeftOutAudio {
  id: string;
  recordingId: RecordingId;
  /** The recording's name when the audio was set aside, where the page knew it. */
  recordingName?: string;
  /** The account the audio was recorded under. It is offered to no other. */
  ownerId: number;
  /** One FLAC file. */
  blob: Blob;
  seconds: number;
  /** When it was set aside, in ms since the epoch. */
  keptAt: number;
}

export interface LeftOutAudioStore {
  put(audio: LeftOutAudio): Promise<void>;
  get(id: string): Promise<LeftOutAudio | undefined>;
  list(): Promise<LeftOutAudio[]>;
  remove(id: string): Promise<void>;
}

/** How long left-out audio waits in the browser for its owner to take it. */
export const LEFT_OUT_AUDIO_KEEP_MS = 7 * 24 * 60 * 60 * 1000;

const DATABASE_NAME = "nojoin-left-out-audio";
const DATABASE_VERSION = 1;
const STORE_NAME = "audio";

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

export class IndexedDbLeftOutAudioStore implements LeftOutAudioStore {
  private database: Promise<IDBDatabase> | null = null;

  constructor(private readonly factory: IDBFactory) {}

  private open() {
    if (!this.database) {
      this.database = new Promise<IDBDatabase>((resolve, reject) => {
        const request = this.factory.open(DATABASE_NAME, DATABASE_VERSION);
        request.onupgradeneeded = () => {
          if (!request.result.objectStoreNames.contains(STORE_NAME)) {
            request.result.createObjectStore(STORE_NAME, { keyPath: "id" });
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

  async put(audio: LeftOutAudio) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).put(audio);
    await transactionDone(transaction);
  }

  async get(id: string) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readonly");
    return requestResult(
      transaction.objectStore(STORE_NAME).get(id) as IDBRequest<LeftOutAudio | undefined>,
    );
  }

  async list() {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readonly");
    const all = await requestResult(
      transaction.objectStore(STORE_NAME).getAll() as IDBRequest<LeftOutAudio[]>,
    );
    return all.sort((left, right) => left.keptAt - right.keptAt);
  }

  async remove(id: string) {
    const database = await this.open();
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).delete(id);
    await transactionDone(transaction);
  }
}

export class MemoryLeftOutAudioStore implements LeftOutAudioStore {
  private readonly audio = new Map<string, LeftOutAudio>();

  async put(audio: LeftOutAudio) {
    this.audio.set(audio.id, audio);
  }

  async get(id: string) {
    return this.audio.get(id);
  }

  async list() {
    return [...this.audio.values()].sort((left, right) => left.keptAt - right.keptAt);
  }

  async remove(id: string) {
    this.audio.delete(id);
  }
}

export const createLeftOutAudioStore = (
  factory: IDBFactory | undefined = typeof indexedDB === "undefined" ? undefined : indexedDB,
): LeftOutAudioStore =>
  factory ? new IndexedDbLeftOutAudioStore(factory) : new MemoryLeftOutAudioStore();

export const describeAudioLength = (seconds: number) =>
  seconds >= 90
    ? `${Math.round(seconds / 60)} minutes`
    : `${Math.max(1, Math.round(seconds))} seconds`;

/** The name the download is saved under. */
export const leftOutAudioFileName = (audio: Pick<LeftOutAudio, "recordingId" | "recordingName">) => {
  // The same characters the audio export keeps.
  const name = (audio.recordingName ?? "").replace(/[^a-zA-Z0-9 \-_.]/g, "").trim();
  return `${name || `recording_${audio.recordingId}`} (left out).flac`;
};

/** Hands the file to the browser's downloads. */
export const saveLeftOutAudio = (audio: LeftOutAudio) => {
  const url = URL.createObjectURL(audio.blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = leftOutAudioFileName(audio);
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoked on the next task: the download has taken its reference by then.
  setTimeout(() => URL.revokeObjectURL(url), 0);
};

const notificationId = (audio: Pick<LeftOutAudio, "id">) => `left-out-audio-${audio.id}`;

const offerMessage = (audio: LeftOutAudio) => {
  const recording = audio.recordingName ? `"${audio.recordingName}"` : "a recording";
  const until = new Date(audio.keptAt + LEFT_OUT_AUDIO_KEEP_MS).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
  });
  return (
    `About ${describeAudioLength(audio.seconds)} of audio this browser recorded for ` +
    `${recording} is not in the recording. Download it to keep a copy. This ` +
    `browser deletes it on ${until}.`
  );
};

export interface OfferLeftOutAudioOptions {
  store: LeftOutAudioStore;
  /** The account signed in, asked only when there is audio to offer. */
  signedInAs: () => Promise<number | undefined>;
  /** Offers closed on this page, which return on the next page load. */
  closed: Set<string>;
  now?: number;
  save?: (audio: LeftOutAudio) => void;
}

/**
 * Removes left-out audio past its time, whoever it belongs to, and offers the
 * rest that belongs to the signed-in account as a download.
 *
 * Each offer stays up until its owner acts. **Download** saves the file and
 * deletes this browser's copy, **Delete** deletes it, and closing the offer
 * hides it until the next page load. A tab is never sure which account the
 * cookie it shares with other tabs belongs to, so an offer is made only once
 * the signed-in account matches the one the audio was recorded under, as
 * removing kept audio for a missing recording waits for (issue #589).
 */
export const offerLeftOutAudio = async ({
  store,
  signedInAs,
  closed,
  now = Date.now(),
  save = saveLeftOutAudio,
}: OfferLeftOutAudioOptions) => {
  const current: LeftOutAudio[] = [];
  for (const audio of await store.list()) {
    if (now - audio.keptAt >= LEFT_OUT_AUDIO_KEEP_MS) {
      await store.remove(audio.id);
    } else {
      current.push(audio);
    }
  }
  const offerable = current.filter((audio) => !closed.has(notificationId(audio)));
  if (offerable.length === 0) {
    return;
  }
  const accountId = await signedInAs();
  const notifications = useNotificationStore.getState();
  for (const audio of offerable) {
    if (audio.ownerId !== accountId) {
      continue;
    }
    const id = notificationId(audio);
    notifications.addNotification({
      id,
      type: "warning",
      persistent: true,
      message: offerMessage(audio),
      actions: [
        {
          label: "Download",
          variant: "primary",
          onClick: () => {
            void downloadLeftOutAudio(store, audio.id, save);
          },
        },
        {
          label: "Delete",
          onClick: () => {
            void store.remove(audio.id).catch((error: unknown) => {
              console.warn("[capture] could not delete left-out audio", error);
            });
          },
        },
      ],
      onClose: () => {
        closed.add(id);
      },
    });
  }
};

/** Saves the file, then deletes this browser's copy. */
const downloadLeftOutAudio = async (
  store: LeftOutAudioStore,
  id: string,
  save: (audio: LeftOutAudio) => void,
) => {
  const notifications = useNotificationStore.getState();
  try {
    // Read again: another tab may have downloaded or deleted it meanwhile.
    const audio = await store.get(id);
    if (!audio) {
      notifications.addNotification({
        type: "info",
        message: "That audio was already downloaded or deleted in another tab.",
      });
      return;
    }
    save(audio);
  } catch (error: unknown) {
    console.warn("[capture] could not download left-out audio", error);
    notifications.addNotification({
      type: "error",
      message:
        "Could not download that audio. Nojoin will offer it again when you next open or reload a page.",
    });
    return;
  }
  await store.remove(id).catch((error: unknown) => {
    console.warn("[capture] could not delete downloaded left-out audio", error);
  });
};
