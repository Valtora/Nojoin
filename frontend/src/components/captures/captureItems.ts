import type { Capture } from "@/lib/api";
import type { PendingCapture } from "@/lib/captures/captureOutbox";
import {
  lineFollowedAt,
  shownLineAt,
  type PlacementSegment,
} from "@/lib/notes/placement";

/** A capture as the views show it: saved, or still on its way from this page. */
export interface CaptureItem {
  key: string;
  captureMs: number;
  capture: Capture | null;
  pending: PendingCapture | null;
}

/** Saved and unsaved captures together, in timeline order. */
export const captureItems = (
  captures: readonly Capture[] | null | undefined,
  pending: readonly PendingCapture[] | null | undefined,
): CaptureItem[] =>
  [
    ...(captures ?? []).map((capture) => ({
      key: `capture-${capture.id}`,
      captureMs: capture.capture_ms,
      capture,
      pending: null,
    })),
    ...(pending ?? []).map((entry) => ({
      key: entry.key,
      captureMs: entry.captureMs,
      capture: null,
      pending: entry,
    })),
  ].sort((left, right) => left.captureMs - right.captureMs);

export interface CapturesAmongLines {
  /** Captures before every shown line. */
  opening: CaptureItem[];
  /** Captures by the index of the shown line they follow. */
  afterLine: Map<number, CaptureItem[]>;
}

/**
 * Where captures show among a view's lines: after the line in progress at
 * their time, by the rule notes follow (`placeTimelineNotes`), and after the
 * shown line before it when that line is hidden.
 */
export const capturesAmongLines = (
  segments: readonly PlacementSegment[],
  items: readonly CaptureItem[],
  visibleIndexes: readonly number[],
): CapturesAmongLines => {
  const opening: CaptureItem[] = [];
  const afterLine = new Map<number, CaptureItem[]>();
  if (items.length === 0) return { opening, afterLine };
  const follows = lineFollowedAt(segments);
  for (const item of items) {
    const lineIndex = shownLineAt(visibleIndexes, follows(item.captureMs));
    if (lineIndex < 0) {
      opening.push(item);
      continue;
    }
    const bucket = afterLine.get(lineIndex);
    if (bucket) bucket.push(item);
    else afterLine.set(lineIndex, [item]);
  }
  return { opening, afterLine };
};

/** A file name for a capture's download, with no character a file system refuses. */
export const captureFileName = (capture: Capture): string => {
  const stamp = capture.title.replace(/^Capture at /, "").replace(/:/g, "-");
  const extension = capture.file_type === "image/jpeg" ? "jpg" : "png";
  return `capture-${stamp}.${extension}`;
};
