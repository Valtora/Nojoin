import { TranscriptSegment } from "@/types";
import { parseServerTimestamp } from "@/lib/serverTimestamp";

/** `mm:ss` for the finished transcript's time column. */
export const formatTime = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, "0")}:${remainingSeconds.toString().padStart(2, "0")}`;
};

/** Expiry of a visible "Revised" badge, or null when the revision is not recent. */
export const recentSegmentExpiry = (segment: TranscriptSegment, now = Date.now()): number | null => {
  const updatedAt = parseServerTimestamp(segment.updated_at);
  if (!updatedAt) return null;
  const age = now - updatedAt.getTime();
  // Allow a small difference between browser and server clocks, not future revisions.
  return age >= -5000 && age < 15000 ? updatedAt.getTime() + 15000 : null;
};
