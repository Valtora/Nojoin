"use client";

import { memo } from "react";

import { cn } from "@/lib/cn";
import { getColorByKey } from "@/lib/constants";
import type { Tag } from "@/types";

/**
 * Slots in the column. A meeting with more tags than this shows one dot
 * fewer and a count of the rest in the last slot, so the column never
 * outgrows the shortest card.
 */
export const TAG_DOT_SLOTS = 3;

/** "Tag: Board" or "Tags: Board, Hiring", naming every tag in order. */
export function tagDotsLabel(tags: readonly Tag[]): string {
  const names = tags.map((tag) => tag.name).join(", ");
  return `${tags.length === 1 ? "Tag" : "Tags"}: ${names}`;
}

interface RecordingTagDotsProps {
  tags: readonly Tag[] | undefined;
  className?: string;
}

/**
 * A meeting's tags as a column of colour dots, in the order they were
 * applied. Hovering names them, and the label carries every name, so the
 * colours are never the only way to tell the tags apart.
 */
function RecordingTagDots({ tags, className }: RecordingTagDotsProps) {
  if (!tags || tags.length === 0) return null;

  const overflows = tags.length > TAG_DOT_SLOTS;
  const shown = overflows ? tags.slice(0, TAG_DOT_SLOTS - 1) : tags;
  const label = tagDotsLabel(tags);

  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={cn("flex cursor-help flex-col items-center gap-0.5", className)}
    >
      {shown.map((tag) => (
        <span
          key={tag.id}
          data-tag-dot=""
          className={cn("size-1.5 shrink-0 rounded-full", getColorByKey(tag.color).dot)}
        />
      ))}
      {overflows && (
        <span className="text-[0.625rem] font-semibold leading-none text-contrast-helper">
          +{tags.length - shown.length}
        </span>
      )}
    </span>
  );
}

export default memo(RecordingTagDots);
