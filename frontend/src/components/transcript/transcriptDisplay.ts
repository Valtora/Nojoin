import { TranscriptSegment } from "@/types";
import { parseServerTimestamp } from "@/lib/serverTimestamp";

/** `mm:ss` for the finished transcript's time column. */
export const formatTime = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, "0")}:${remainingSeconds.toString().padStart(2, "0")}`;
};

/** How long a line shows "Revised" after the write that changed it. */
const REVISED_BADGE_MS = 15_000;

/** Expiry of a "Revised" badge for a change stamped `changedAt`, or null when it is not recent. */
export const revisedBadgeExpiry = (changedAt: string | null | undefined, now = Date.now()): number | null => {
  const stamp = parseServerTimestamp(changedAt);
  if (!stamp) return null;
  const age = now - stamp.getTime();
  // Allow a small difference between browser and server clocks, not future revisions.
  return age >= -5000 && age < REVISED_BADGE_MS ? stamp.getTime() + REVISED_BADGE_MS : null;
};

/**
 * What a line showed as of its last server write, and the stamp of the last
 * write that changed it after the view first showed it.
 */
export interface LineChange {
  stamp: string | null;
  text: string;
  speaker: string;
  changedAt: string | null;
}

export type LineChanges = ReadonlyMap<string, LineChange>;

export const NO_LINE_CHANGES: LineChanges = new Map();

/**
 * `previous` brought up to date with `items`. A line the view has not shown
 * before is recorded as it is, unchanged: lines written moments ago (a sample
 * meeting, a capture's or an import's first transcript) are new to the reader,
 * not revised. A later write counts as a change only when it alters the line's
 * text or speaker. The comparison waits for the server's stamp, so a local
 * edit counts once it is saved, as it did before. Lines that leave the view
 * keep their record, so one that returns is not new again.
 */
export function trackLineChanges(
  previous: LineChanges,
  items: readonly { segment: TranscriptSegment; segmentId: string }[],
): LineChanges {
  let next: Map<string, LineChange> | null = null;
  for (const { segment, segmentId } of items) {
    const stamp = segment.updated_at ?? null;
    const known = previous.get(segmentId);
    if (known !== undefined && known.stamp === stamp) continue;
    const changed = known !== undefined && (known.text !== segment.text || known.speaker !== segment.speaker);
    next ??= new Map(previous);
    next.set(segmentId, {
      stamp,
      text: segment.text,
      speaker: segment.speaker,
      changedAt: changed ? stamp : known?.changedAt ?? null,
    });
  }
  return next ?? previous;
}
