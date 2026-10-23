import { noteSnippet } from "@/lib/notes/noteOutbox";
import { resolveSelection, type Resolution } from "@/lib/notes/resolveSelection";
import type { DrawnLine } from "@/lib/notes/selectionRange";

import type { NoteDecoration } from "./textDecorations";

/** What highlighting reads of a note. */
export interface HighlightNote {
  id: string;
  kind: string;
  body: string;
  start_ms?: number | null;
  end_ms?: number | null;
  quoted_text?: string | null;
}

export interface NoteHighlights {
  /** Each line's note marks, by its key; a line without any is absent. */
  byLine: ReadonlyMap<string, readonly NoteDecoration[]>;
  /** Where each selection note's words were found, by note id. */
  byNote: ReadonlyMap<string, Resolution>;
}

const titleOf = (note: HighlightNote, resolution: Resolution) => {
  const title = `Note: “${noteSnippet(note.body)}”`;
  return resolution.state === "fuzzy"
    ? `${title}. Written on “${note.quoted_text ?? ""}”, since reworded.`
    : title;
};

/**
 * The marks the selection notes put on the lines shown: each note resolved
 * against them as they read now, its words marked when found exactly or with
 * changes, and nothing marked when not found.
 */
export function noteHighlights(
  notes: readonly HighlightNote[],
  lines: readonly DrawnLine[],
): NoteHighlights {
  const byLine = new Map<string, NoteDecoration[]>();
  const byNote = new Map<string, Resolution>();
  for (const note of notes) {
    if (note.kind !== "selection") continue;
    const resolution = resolveSelection(note, lines);
    byNote.set(note.id, resolution);
    const title = titleOf(note, resolution);
    for (const range of resolution.ranges) {
      const decoration: NoteDecoration = {
        kind: "note",
        from: range.from,
        to: range.to,
        noteId: note.id,
        fuzzy: resolution.state === "fuzzy",
        title,
      };
      const marks = byLine.get(range.segmentId);
      if (marks) marks.push(decoration);
      else byLine.set(range.segmentId, [decoration]);
    }
  }
  return { byLine, byNote };
}

/** The text of `ranges` in `lines`, joined: what an unanchored note's span reads now. */
export const textOfRanges = (
  ranges: Resolution["bestGuess"],
  lines: readonly DrawnLine[],
): string => {
  const textById = new Map(lines.map((line) => [line.segmentId, line.text]));
  return ranges
    .map((range) => textById.get(range.segmentId)?.slice(range.from, range.to) ?? "")
    .filter((text) => text !== "")
    .join(" ");
};
