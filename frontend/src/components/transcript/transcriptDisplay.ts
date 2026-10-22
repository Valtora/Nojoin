import { TranscriptSegment } from "@/types";

/** `mm:ss` for the finished transcript's time column. */
export const formatTime = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return `${minutes.toString().padStart(2, "0")}:${remainingSeconds.toString().padStart(2, "0")}`;
};

/** Revised in the last 15 s: the line shows "Revised", and its lane keeps room for it. */
export const isRecentlyUpdatedSegment = (segment: TranscriptSegment) => {
  return (
    typeof segment.updated_at === "string" &&
    Date.now() - new Date(segment.updated_at).getTime() < 15000
  );
};
