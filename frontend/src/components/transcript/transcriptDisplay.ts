import { TranscriptSegment } from "@/types";
import { parseServerTimestamp } from "@/lib/serverTimestamp";

/** `mm:ss` for the finished transcript's time column. */
export const formatTime = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, "0")}:${remainingSeconds.toString().padStart(2, "0")}`;
};

/** Revised in the last 15 s: the line shows "Revised", and its lane keeps room for it. */
export const isRecentlyUpdatedSegment = (segment: TranscriptSegment) => {
  const updatedAt = parseServerTimestamp(segment.updated_at);
  if (!updatedAt) return false;
  const age = Date.now() - updatedAt.getTime();
  // Allow a small difference between browser and server clocks, not future revisions.
  return age >= -5000 && age < 15000;
};
