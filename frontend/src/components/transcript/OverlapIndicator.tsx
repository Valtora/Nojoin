"use client";

import { createContext, useContext, useId, useState } from "react";
import { ButtonBase } from "@/components/ui/nativeBases";
import { segmentSpeakerDisplayName } from "@/lib/recordingSpeakerUtils";
import type { TranscriptSegment } from "@/types";
import { TOUCH_TARGET_HEIGHT } from "@/components/ui/touchTarget";

/** Actual simultaneous lines, indexed once when the transcript changes. */
export function indexOverlaps(segments: readonly TranscriptSegment[]): Map<TranscriptSegment, TranscriptSegment[]> {
  const result = new Map<TranscriptSegment, TranscriptSegment[]>();
  let active: TranscriptSegment[] = [];
  for (const segment of [...segments].sort((a, b) => a.start - b.start)) {
    if (segment.end <= segment.start) continue;
    active = active.filter((other) => other.end > segment.start);
    for (const other of active) {
      if (other.speaker === segment.speaker || segment.end <= other.start) continue;
      result.set(segment, [...(result.get(segment) ?? []), other]);
      result.set(other, [...(result.get(other) ?? []), segment]);
    }
    active.push(segment);
  }
  return result;
}

export const NO_OVERLAPS: readonly TranscriptSegment[] = [];
export const OverlapContext = createContext<ReadonlyMap<TranscriptSegment, readonly TranscriptSegment[]>>(new Map());

/** Secondary voices stay on the credited line; only real separate text is quoted. */
export default function OverlapIndicator({ segment, overlaps, speakerMap }: {
  segment: TranscriptSegment;
  overlaps?: readonly TranscriptSegment[];
  speakerMap: Record<string, string>;
}) {
  const id = useId();
  const indexed = useContext(OverlapContext);
  const peers = overlaps ?? indexed.get(segment) ?? NO_OVERLAPS;
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const labels = [...new Set([
    ...(segment.overlapping_speakers ?? []), ...peers.map((other) => other.speaker),
  ])].filter((label) => label !== segment.speaker && label !== "UNKNOWN");
  if (labels.length === 0) return null;
  const open = hovered || pinned;
  return (
    <div
      className="min-w-0 text-sm"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onKeyDown={(event) => {
        if (event.key === "Escape") { setPinned(false); setHovered(false); }
      }}
    >
      <ButtonBase
        type="button"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => { setPinned(!pinned); setHovered(false); }}
        className={`rounded px-2 text-xs font-medium text-action-text hover:bg-action-tint ${TOUCH_TARGET_HEIGHT}`}
      >
        Overlap
      </ButtonBase>
      {open && (
        <div id={id} role="region" aria-label="Overlapping speech" className="space-y-1 border-l-2 border-action-border pl-2 text-contrast-muted">
          {labels.map((label) => {
            const lines = peers.filter((other) => other.speaker === label);
            return <p key={label} className="whitespace-pre-wrap break-words">
              <strong>{segmentSpeakerDisplayName(speakerMap, label)}: </strong>
              {lines.length ? lines.map((other) => other.text).join(" ") : "No separately transcribed words."}
            </p>;
          })}
        </div>
      )}
    </div>
  );
}
