import type { TimelineNoteKind } from "@/types";

import { stripNote } from "./headNoteSync";

/**
 * Where a recording's timeline notes sit among its transcript lines: the twin
 * of `place_timeline_notes` in `backend/utils/timeline_notes.py`, for the views
 * that place notes among their own lines. Both implementations run the golden
 * cases in `backend/tests/fixtures/timeline_note_placement_cases.json`, so a
 * change to the rule is a change to that file and to both.
 *
 * The rule, for a body note (`point` or `selection`) at time t:
 *
 * - The note follows the line in progress at t: the line with the largest list
 *   index whose start is at or before t. Before the first line it opens the
 *   transcript (`afterIndex === -1`).
 * - A point note's time is its `anchor_ms`; a selection's is
 *   `max(start_ms, end_ms - 1)`.
 * - Lines are taken in list order, never re-sorted. A blank line is never an
 *   anchor.
 * - Placement runs on the whole list; a view that hides lines renders a note
 *   after the visible line before it (`bodyNotesAmongLines`).
 *
 * `head` notes come first and `tail` notes last. Ties break by creation time,
 * then id.
 */

export type NoteSlot = "head" | "body" | "tail";

/** What placement reads of a transcript line. */
export interface PlacementSegment {
  start_ms?: number | null;
  /** Seconds; read only when `start_ms` is absent. */
  start?: number | null;
  text?: string | null;
}

/** What placement reads of a note. */
export interface PlacementNote {
  id: string;
  kind: TimelineNoteKind;
  anchor_ms?: number | null;
  start_ms?: number | null;
  end_ms?: number | null;
  created_at: string;
}

export interface NotePlacement<N extends PlacementNote> {
  note: N;
  slot: NoteSlot;
  /** Body notes only: the index of the line the note follows, -1 before the first. */
  afterIndex: number;
  /** Body notes only: the note's placement time. */
  atMs: number | null;
}

// Python's round(): to the nearest integer, halves to the even one.
const roundHalfEven = (value: number): number => {
  const floor = Math.floor(value);
  const fraction = value - floor;
  if (fraction > 0.5) return floor + 1;
  if (fraction < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
};

/** A line's start in ms: `start_ms`, else `start` in seconds. */
export const segmentStartMs = (segment: PlacementSegment): number => {
  if (segment.start_ms !== null && segment.start_ms !== undefined) {
    return Math.trunc(segment.start_ms);
  }
  return roundHalfEven(Number(segment.start || 0) * 1000);
};

/** When a body note happened on the audio timeline. */
export const noteTimeMs = (note: PlacementNote): number => {
  if (note.kind === "selection") {
    if (note.start_ms == null || note.end_ms == null) {
      throw new Error(`selection note ${note.id} has no range`);
    }
    return Math.max(note.start_ms, note.end_ms - 1);
  }
  if (note.anchor_ms == null) {
    throw new Error(`point note ${note.id} has no anchor`);
  }
  return note.anchor_ms;
};

const ISO_DATETIME =
  /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:?\d{2})?$/i;

/**
 * A creation time as microseconds since the epoch, the precision the API
 * sends, so two notes made in the same millisecond still order as they do
 * on the server. A time without a zone is UTC, as the API's are.
 */
export const createdAtMicros = (value: string): number => {
  const match = ISO_DATETIME.exec(value.trim());
  if (!match) {
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? 0 : parsed * 1000;
  }
  const [, year, month, day, hour, minute, second = "0", fraction = "", zone] =
    match;
  let ms = Date.UTC(+year, +month - 1, +day, +hour, +minute, +second);
  if (zone && zone.toUpperCase() !== "Z") {
    const digits = zone.slice(1).replace(":", "");
    const offsetMinutes = Number(digits.slice(0, 2)) * 60 + Number(digits.slice(2, 4));
    ms -= (zone[0] === "-" ? -1 : 1) * offsetMinutes * 60_000;
  }
  return ms * 1000 + Number((fraction + "000000").slice(0, 6));
};

// Code-unit order, as Python compares str, never the locale's.
const compareIds = (left: string, right: string) =>
  left < right ? -1 : left > right ? 1 : 0;

const byCreation = <N extends PlacementNote>(
  left: NotePlacement<N>,
  right: NotePlacement<N>,
) =>
  createdAtMicros(left.note.created_at) - createdAtMicros(right.note.created_at) ||
  compareIds(left.note.id, right.note.id);

/** The index of the first element greater than `value` in an ascending list. */
const bisectRight = (sorted: readonly number[], value: number): number => {
  let low = 0;
  let high = sorted.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (value < sorted[middle]) high = middle;
    else low = middle + 1;
  }
  return low;
};

/** Answers "largest eligible index starting at or before t". */
class AnchorIndex {
  private readonly starts: number[] = [];
  private readonly bestIndex: number[] = [];

  constructor(segments: readonly PlacementSegment[]) {
    const eligible = segments
      .map((segment, index) => ({ start: segmentStartMs(segment), index, segment }))
      .filter(({ segment }) => stripNote(String(segment.text ?? "")) !== "")
      .sort((left, right) => left.start - right.start || left.index - right.index);
    let best = -1;
    for (const { start, index } of eligible) {
      best = Math.max(best, index);
      this.starts.push(start);
      this.bestIndex.push(best);
    }
  }

  afterIndex(atMs: number): number {
    const position = bisectRight(this.starts, atMs);
    return position ? this.bestIndex[position - 1] : -1;
  }
}

/** Every note with its slot, in timeline order: head, body, tail. */
export function placeTimelineNotes<N extends PlacementNote>(
  segments: readonly PlacementSegment[],
  notes: Iterable<N>,
): NotePlacement<N>[] {
  const heads: NotePlacement<N>[] = [];
  const bodies: NotePlacement<N>[] = [];
  const tails: NotePlacement<N>[] = [];
  const anchors = new AnchorIndex(segments);
  for (const note of notes) {
    if (note.kind === "head") {
      heads.push({ note, slot: "head", afterIndex: -1, atMs: null });
    } else if (note.kind === "tail") {
      tails.push({ note, slot: "tail", afterIndex: -1, atMs: null });
    } else {
      const atMs = noteTimeMs(note);
      bodies.push({ note, slot: "body", afterIndex: anchors.afterIndex(atMs), atMs });
    }
  }
  heads.sort(byCreation);
  tails.sort(byCreation);
  bodies.sort((left, right) => (left.atMs ?? 0) - (right.atMs ?? 0) || byCreation(left, right));
  return [...heads, ...bodies, ...tails];
}

export interface NotesAmongLines<N> {
  /** Body notes shown before every visible line, in timeline order. */
  opening: N[];
  /** Body notes by the index of the visible line they follow, in timeline order. */
  afterLine: Map<number, N[]>;
}

/**
 * The body notes a view shows among the lines it shows.
 *
 * `visibleIndexes` are the list indexes (into the list the notes were placed
 * on) of the lines the view renders, ascending. A note that follows a hidden
 * line shows after the visible line before it, and one before every visible
 * line opens the view, so hiding a line never hides a note.
 */
export function bodyNotesAmongLines<N extends PlacementNote>(
  placements: readonly NotePlacement<N>[],
  visibleIndexes: readonly number[],
): NotesAmongLines<N> {
  const opening: N[] = [];
  const afterLine = new Map<number, N[]>();
  for (const placement of placements) {
    if (placement.slot !== "body") continue;
    const position = bisectRight(visibleIndexes, placement.afterIndex) - 1;
    if (position < 0) {
      opening.push(placement.note);
      continue;
    }
    const lineIndex = visibleIndexes[position];
    const bucket = afterLine.get(lineIndex);
    if (bucket) bucket.push(placement.note);
    else afterLine.set(lineIndex, [placement.note]);
  }
  return { opening, afterLine };
}

/** `MM:SS` on the audio timeline, minutes uncapped, as the notes prompts write it. */
export const formatTimelineMs = (ms: number): string => {
  const seconds = Math.floor(Math.max(Math.trunc(ms), 0) / 1000);
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
};
