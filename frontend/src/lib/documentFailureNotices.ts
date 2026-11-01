/**
 * Which document failures this browser has already announced.
 *
 * A document that failed to parse keeps its error, and the Documents view
 * mounts with every visit to its recording. Remembered per mount, the error's
 * notification came back on each visit and went into the notification history
 * each time (issue #887). Kept in local storage instead, a failure is announced
 * once per document and message, across visits, reloads and tabs; the table
 * still marks it Failed.
 */

const STORAGE_KEY = "nojoin-document-failures-announced";
/** Enough for any recent failure; the oldest are forgotten first. */
const MAX_REMEMBERED = 200;

/** What local storage refused to keep, remembered for this page load. */
let unstored: string[] = [];

function load(): string[] {
  let stored: unknown = [];
  try {
    stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]");
  } catch {
    // Unreadable or blocked: only this page load's entries are known.
  }
  const entries = Array.isArray(stored)
    ? stored.filter((entry): entry is string => typeof entry === "string")
    : [];
  return [...entries, ...unstored.filter((entry) => !entries.includes(entry))];
}

function save(entries: string[]): void {
  const kept = entries.slice(-MAX_REMEMBERED);
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(kept));
    unstored = [];
  } catch {
    unstored = kept;
  }
}

/**
 * True the first time this browser sees a document fail with a message, and
 * false from then on, so the caller announces each failure once.
 */
export function claimDocumentFailureNotice(documentId: number, message: string): boolean {
  const entries = load();
  const key = `${documentId}:${message}`;
  if (entries.includes(key)) return false;
  save([...entries, key]);
  return true;
}

/** Forgets a document's failures, so one after Parse again is announced. */
export function forgetDocumentFailureNotices(documentId: number): void {
  const prefix = `${documentId}:`;
  const entries = load();
  if (entries.some((entry) => entry.startsWith(prefix))) {
    save(entries.filter((entry) => !entry.startsWith(prefix)));
  }
}
