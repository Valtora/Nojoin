"use client";

import type { MouseEventHandler, ReactNode } from "react";

import { buildTextRuns, type TextDecoration, type TextRun } from "./textDecorations";

interface TranscriptLineTextProps {
  text: string;
  /** The line's key, as `getTranscriptSegmentKey` gives it. */
  segmentId: string;
  /** The line's span on the audio timeline, in ms. */
  startMs: number;
  endMs: number;
  decorations?: readonly TextDecoration[];
  className: string;
  title?: string;
  onClick?: MouseEventHandler<HTMLParagraphElement>;
}

const NO_DECORATIONS: readonly TextDecoration[] = [];

/** How a run draws: `null` for plain text. */
type SearchLook = "match" | "current" | null;

const lookOf = (decorations: readonly TextDecoration[]): SearchLook => {
  let look: SearchLook = null;
  for (const decoration of decorations) {
    switch (decoration.kind) {
      case "search":
        look = look === "current" || decoration.current ? "current" : "match";
        break;
      default: {
        // A new kind fails to compile here until it has a look.
        const unhandled: never = decoration.kind;
        return unhandled;
      }
    }
  }
  return look;
};

/**
 * Joins neighbouring runs that draw alike and share a decoration, so matches
 * that overlap read as one mark per look rather than a mark per character.
 * Matches that only touch stay apart, as separate matches always drew.
 */
const joinRuns = (runs: readonly TextRun[]): TextRun[] => {
  const joined: TextRun[] = [];
  runs.forEach((run) => {
    const previous = joined[joined.length - 1];
    const drawsAsOne =
      previous !== undefined &&
      previous.from < previous.to &&
      run.from < run.to &&
      lookOf(previous.decorations) === lookOf(run.decorations) &&
      previous.decorations.some((decoration) => run.decorations.includes(decoration));
    if (!drawsAsOne) {
      joined.push(run);
      return;
    }
    joined[joined.length - 1] = {
      from: previous.from,
      to: run.to,
      decorations: [
        ...previous.decorations,
        ...run.decorations.filter((decoration) => !previous.decorations.includes(decoration)),
      ],
    };
  });
  return joined;
};

const renderRun = (text: string, run: TextRun, index: number): ReactNode => {
  const slice = text.slice(run.from, run.to);
  const look = lookOf(run.decorations);
  if (look === null) {
    return slice;
  }
  return (
    <mark
      key={index}
      className={`${look === "current" ? "bg-action text-action-on" : "bg-status-warning-bg text-status-warning-fg"} rounded-sm px-0.5`}
    >
      {slice}
    </mark>
  );
};

/**
 * A transcript line's text, shared by the live and the finished view.
 *
 * The paragraph names its line and the line's span on the audio timeline in
 * `data-segment-id`, `data-start-ms` and `data-end-ms`, so code that reads the
 * page (a text selection, say) can map what it finds back to a line and a time
 * without knowing which view drew it. `decorations` mark ranges of the text;
 * the paragraph's text is always the line's text, each character once.
 */
export default function TranscriptLineText({
  text,
  segmentId,
  startMs,
  endMs,
  decorations = NO_DECORATIONS,
  className,
  title,
  onClick,
}: TranscriptLineTextProps) {
  const runs = decorations.length === 0 ? [] : joinRuns(buildTextRuns(text, decorations));
  const isPlain = runs.every((run) => run.decorations.length === 0);

  return (
    <p
      data-segment-id={segmentId}
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
