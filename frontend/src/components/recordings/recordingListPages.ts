import type { Recording } from "@/types";
import { parseServerTimestamp } from "@/lib/serverTimestamp";

/**
 * How the recordings list pages, as pure functions over its rows, for
 * {@link useRecordingListPages}.
 *
 * The server orders the list newest first, by `created_at` and then by its
 * own id, so offset pages follow one fixed order.
 */

/**
 * Rows per page. Each row is about 1 KB of JSON and a card about 80 px high,
 * so a page fills several screens of the rail and reads in about 15 ms on a
 * library of a thousand meetings.
 */
export const RECORDINGS_PAGE_SIZE = 50;

/**
 * The 15 s poll re-reads at most this many of the rows loaded, the 100 the
 * list read on every poll before it paged. Rows further down keep what they
 * last showed until an action or update re-reads the whole range.
 */
export const POLL_REFRESH_ROWS = 100;

/**
 * Each further page starts one row early. The first row it returns should be
 * the last row already shown; when it is not, a meeting was added or removed
 * above while the list was being scrolled, and the loaded range is read again.
 */
export const PAGE_OVERLAP = 1;

/** Negative when `a` started before `b`, by the server's `created_at`. */
const compareCreatedAt = (a: Recording, b: Recording): number => {
  const aTime = parseServerTimestamp(a.created_at)?.getTime();
  const bTime = parseServerTimestamp(b.created_at)?.getTime();
  if (aTime !== undefined && bTime !== undefined && aTime !== bTime) {
    return aTime - bTime;
  }
  // Same millisecond: the strings keep the microseconds, and a shorter
  // fraction of the same second sorts first, as it is the smaller time.
  return a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0;
};

/**
 * The rows after reading the first `limit` of the list again.
 *
 * The answer is the truth for the stretch of the list it covers, from the
 * top down to its last row: a loaded row in that stretch that it leaves out
 * has been deleted, moved or filtered away. Rows older than its last row are
 * beyond what it covers and stay, so a new meeting at the top never pushes a
 * loaded one off the bottom. A short answer is the whole list.
 */
export function mergeRefresh(
  current: Recording[],
  fresh: Recording[],
  limit: number,
): Recording[] {
  const kept = keepUnchanged(current, fresh);
  if (fresh.length < limit) return sameRows(current, kept) ? current : kept;
  const last = fresh[fresh.length - 1];
  const freshIds = new Set(fresh.map((recording) => recording.id));
  // A row that started at the same instant as the last one could fall on
  // either side of it, so it is kept rather than dropped.
  const beyond = current.filter(
    (recording) =>
      !freshIds.has(recording.id) && compareCreatedAt(recording, last) <= 0,
  );
  const merged = [...kept, ...beyond];
  return sameRows(current, merged) ? current : merged;
}

/**
 * `fresh`, with each row that reads the same as the one shown replaced by
 * the one shown. A poll that finds nothing changed then hands back the very
 * rows the list holds, and a long list is not drawn again every 15 s.
 */
function keepUnchanged(current: Recording[], fresh: Recording[]): Recording[] {
  const shown = new Map(current.map((recording) => [recording.id, recording]));
  return fresh.map((recording) => {
    const held = shown.get(recording.id);
    return held && JSON.stringify(held) === JSON.stringify(recording) ? held : recording;
  });
}

const sameRows = (a: Recording[], b: Recording[]): boolean =>
  a.length === b.length && a.every((recording, index) => recording === b[index]);

/** The rows with the next page added below them, each meeting once. */
export function appendPage(current: Recording[], page: Recording[]): Recording[] {
  const shown = new Set(current.map((recording) => recording.id));
  const added = page.filter((recording) => !shown.has(recording.id));
  return added.length === 0 ? current : [...current, ...added];
}

/**
 * Whether a page fetched from `current.length - PAGE_OVERLAP` lines up with
 * the rows shown: its first row is the last one shown.
 */
export function pageLinesUp(current: Recording[], page: Recording[]): boolean {
  if (current.length === 0) return true;
  return page.length > 0 && page[0].id === current[current.length - 1].id;
}

/** Rows to read again: the loaded range, at least a page, at most `cap`. */
export function refreshLimit(loaded: number, cap = Number.POSITIVE_INFINITY): number {
  const pages = Math.max(1, Math.ceil(loaded / RECORDINGS_PAGE_SIZE));
  return Math.min(pages * RECORDINGS_PAGE_SIZE, Math.max(cap, RECORDINGS_PAGE_SIZE));
}
