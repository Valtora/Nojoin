"use client";

import type { ReactNode } from "react";

import {
  buildTextRuns,
  type NoteDecoration,
  type TextDecoration,
  type TextRun,
} from "./textDecorations";

interface TranscriptLineTextProps {
  text: string;
  /** The line's key, as `getTranscriptSegmentKey` gives it. */
  segmentId: string;
  /** The line's span on the audio timeline, in ms. */
  startMs: number;
  endMs: number;
  revision?: number;
  settlingRequired?: boolean;
  settling?: boolean;
  decorations?: readonly TextDecoration[];
  className: string;
}

const NO_DECORATIONS: readonly TextDecoration[] = [];

/**
 * How a run draws: `null` for plain text. A search match draws over a note's
 * words, since it is what the reader is looking for now; a note's words found
 * with changes draw as such when any note on them was.
 */
type Look = "match" | "current" | "note" | "note-changed" | null;

const lookOf = (decorations: readonly TextDecoration[]): Look => {
  let look: Look = null;
  for (const decoration of decorations) {
    switch (decoration.kind) {
      case "search":
        look = look === "current" || decoration.current ? "current" : "match";
        break;
      case "note":
        if (look === "match" || look === "current") break;
        look = look === "note-changed" || decoration.fuzzy ? "note-changed" : "note";
        break;
      default: {
        // A new kind fails to compile here until it has a look.
        const unhandled: never = decoration;
        return unhandled;
      }
    }
  }
  return look;
};

const SEARCH_CLASSES = {
  match: "bg-status-warning-bg text-status-warning-fg",
  current: "bg-action text-action-on",
};

/** The first note on a run, which a click on it edits. */
const noteOf = (decorations: readonly TextDecoration[]) =>
  decorations.find((decoration): decoration is NoteDecoration => decoration.kind === "note");

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
  if (look === "match" || look === "current") {
    return (
      <mark key={index} className={`${SEARCH_CLASSES[look]} rounded-sm px-0.5`}>
        {slice}
      </mark>
    );
  }
  const note = noteOf(run.decorations);
  // No padding: a note's words keep their place, so the line reads as it did.
  // Underlined as well as tinted, since a bubble can be the tint's own colour
  // (an orange speaker); dotted where the words have changed.
  return (
    <mark
      key={index}
      data-note-id={note?.noteId}
      data-note-anchor={look === "note-changed" ? "fuzzy" : "exact"}
      title={note?.title}
      className={`cursor-pointer rounded-sm bg-action-tint text-action-tint-fg underline decoration-action-text decoration-2 underline-offset-4 ${
        look === "note-changed" ? "decoration-dotted" : "decoration-solid"
      }`}
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
  revision,
  settlingRequired,
  settling,
  decorations = NO_DECORATIONS,
  className,
}: TranscriptLineTextProps) {
  const runs = decorations.length === 0 ? [] : joinRuns(buildTextRuns(text, decorations));
  const isPlain = runs.every((run) => run.decorations.length === 0);

  return (
    <p
      data-segment-id={segmentId}
      data-start-ms={startMs}
      data-end-ms={endMs}
      data-revision={settlingRequired ? revision : undefined}
      data-settling={settlingRequired ? String(!!settling) : undefined}
      className={className}
    >
      {isPlain ? text : runs.map((run, index) => renderRun(text, run, index))}
    </p>
  );
}
