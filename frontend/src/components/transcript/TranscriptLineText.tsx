"use client";

import type { ReactNode } from "react";
import type { LiveTranscriptWord } from "@/types";
import { wordRanges, type WordRange } from "@/lib/transcript/wordRanges";

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
  settlingRequired?: boolean;
  settling?: boolean;
  words?: readonly LiveTranscriptWord[];
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

const decoratedText = (text: string, decorations: readonly TextDecoration[]): ReactNode => {
  const runs = decorations.length === 0 ? [] : joinRuns(buildTextRuns(text, decorations));
  return runs.every((run) => run.decorations.length === 0)
    ? text : runs.map((run, index) => renderRun(text, run, index));
};

/** Keep each word's identity around its decorated text, for exact selections. */
const settledText = (
  text: string, ranges: readonly WordRange[], decorations: readonly TextDecoration[],
): ReactNode => {
  const slice = (from: number, to: number) => decoratedText(text.slice(from, to), decorations
    .filter((decoration) => decoration.from === decoration.to
      ? decoration.from >= from && (decoration.from < to || (to === text.length && decoration.from === to))
      : decoration.from < to && decoration.to > from)
    .map((decoration) => ({ ...decoration,
      from: Math.max(0, decoration.from - from), to: Math.min(to - from, decoration.to - from) })));
  let cursor = 0;
  const nodes: ReactNode[] = [];
  for (const { word, from, to } of ranges) {
    if (from > cursor) nodes.push(<span key={`gap-${word.id}`}>{slice(cursor, from)}</span>);
    nodes.push(
      <span key={word.id} data-word-id={word.id} data-word-from={from} data-word-to={to}
        data-word-start-ms={word.start_ms ?? undefined} data-word-end-ms={word.end_ms ?? undefined}
        data-word-locked={String(word.locked)}
        className={word.locked ? "not-italic" : "italic text-contrast-helper"}>
        {slice(from, to)}
      </span>,
    );
    cursor = to;
  }
  if (cursor < text.length) nodes.push(<span key="trailing">{slice(cursor, text.length)}</span>);
  return nodes;
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
  settlingRequired,
  settling,
  words,
  decorations = NO_DECORATIONS,
  className,
}: TranscriptLineTextProps) {
  const ranges = settlingRequired && words ? wordRanges(text, words) : [];

  return (
    <p
      data-segment-id={segmentId}
      data-start-ms={startMs}
      data-end-ms={endMs}
      data-settling={settlingRequired ? String(!!settling) : undefined}
      className={className}
    >
      {ranges.length ? settledText(text, ranges, decorations) : decoratedText(text, decorations)}
    </p>
  );
}
