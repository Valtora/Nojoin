"use client";

import type { MouseEventHandler, ReactNode } from "react";

import { buildTextRuns, type TextDecoration, type TextRun } from "./textDecorations";

interface TranscriptLineTextProps {
  text: string;
  /** The line's key, as `getTranscriptSegmentKey` gives it. */
  segmentKey: string;
  /** The line's span on the audio timeline, in ms. */
  startMs: number;
  endMs: number;
  decorations?: readonly TextDecoration[];
  className: string;
  title?: string;
  onClick?: MouseEventHandler<HTMLParagraphElement>;
}

const NO_DECORATIONS: readonly TextDecoration[] = [];

const renderRun = (text: string, run: TextRun, index: number): ReactNode => {
  const slice = text.slice(run.from, run.to);
  const search = run.decorations.filter((decoration) => decoration.kind === "search");
  if (search.length === 0) {
    return slice;
  }
  const isCurrent = search.some((decoration) => decoration.current);
  return (
    <mark
      key={index}
      className={`${isCurrent ? "bg-action text-action-on" : "bg-status-warning-bg text-status-warning-fg"} rounded-sm px-0.5`}
    >
      {slice}
    </mark>
  );
};

/**
 * A transcript line's text, shared by the live and the finished view.
 *
 * The paragraph names its line and the line's span on the audio timeline in
 * `data-segment-key`, `data-start-ms` and `data-end-ms`, so code that reads the
 * page (a text selection, say) can map what it finds back to a line and a time
 * without knowing which view drew it. `decorations` mark ranges of the text.
 */
export default function TranscriptLineText({
  text,
  segmentKey,
  startMs,
  endMs,
  decorations = NO_DECORATIONS,
  className,
  title,
  onClick,
}: TranscriptLineTextProps) {
  const runs = buildTextRuns(text, decorations);
  const isPlain = runs.every((run) => run.decorations.length === 0);

  return (
    <p
      data-segment-key={segmentKey}
      data-start-ms={startMs}
      data-end-ms={endMs}
      className={className}
      onClick={onClick}
      title={title}
    >
      {isPlain ? text : runs.map((run, index) => renderRun(text, run, index))}
    </p>
  );
}
