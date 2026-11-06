"use client";

import { memo } from "react";

import { cn } from "@/lib/cn";
import { getColorByKey } from "@/lib/constants";
import type { Tag } from "@/types";

/** Dots in one column: as many as the shortest card, a title and a meta line, holds. */
export const TAG_DOT_ROWS = 6;

/**
 * Slots in both columns. A meeting with more tags than this shows one dot
 * fewer and a count of the rest in the last slot.
 */
export const TAG_DOT_SLOTS = TAG_DOT_ROWS * 2;

export interface TagDotsLayout {
  /** 0 for an untagged meeting. */
  columns: 0 | 1 | 2;
  /** Slots in the first column, which is never the shorter one. */
  rows: number;
  dots: number;
  /** Tags left out of the dots, counted in the last slot. */
  more: number;
}

/**
 * One column up to `TAG_DOT_ROWS`, then two split evenly, and past two full
 * columns the last slot counts the rest.
 */
export function tagDotsLayout(count: number): TagDotsLayout {
  if (count <= 0) return { columns: 0, rows: 0, dots: 0, more: 0 };
  if (count <= TAG_DOT_ROWS) return { columns: 1, rows: count, dots: count, more: 0 };
  if (count <= TAG_DOT_SLOTS) {
    return { columns: 2, rows: Math.ceil(count / 2), dots: count, more: 0 };
  }
  const dots = TAG_DOT_SLOTS - 1;
  return { columns: 2, rows: TAG_DOT_ROWS, dots, more: count - dots };
}

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
 * A meeting's tags as colour dots, in the order they were applied, down the
 * first column and then the second. Hovering names them, and the label
 * carries every name, so the colours are never the only way to tell the tags
 * apart.
 */
function RecordingTagDots({ tags, className }: RecordingTagDotsProps) {
  if (!tags || tags.length === 0) return null;

  const { rows, dots, more } = tagDotsLayout(tags.length);
  const label = tagDotsLabel(tags);
  const slots = tags.slice(0, dots).map((tag) => (
    <span
      key={tag.id}
      data-tag-dot=""
      className={cn("size-1.5 shrink-0 rounded-full", getColorByKey(tag.color).dot)}
    />
  ));
  if (more > 0) {
    slots.push(
      <span
        key="more"
        className="text-[0.625rem] font-semibold leading-none tabular-nums text-contrast-helper"
      >
        +{more}
      </span>,
    );
  }
  const columns = [slots.slice(0, rows), slots.slice(rows)].filter((column) => column.length > 0);

  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={cn("flex cursor-help items-start gap-1", className)}
    >
      {columns.map((column, index) => (
        <span key={index} data-tag-dot-column="" className="flex flex-col items-center gap-0.5">
          {column}
        </span>
      ))}
    </span>
  );
}

export default memo(RecordingTagDots);
