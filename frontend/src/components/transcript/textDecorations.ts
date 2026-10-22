/**
 * Decorations on a transcript line's text: character ranges that render as
 * marks over the words, such as a search match.
 *
 * `from` and `to` are offsets into the line's text, half-open. A decoration
 * with `from === to` marks a point: it renders as an empty element there, which
 * is what a zero-width regex match shows. A range that runs past either end is
 * cut to the text; one with `to < from`, or none of it on the text, is dropped.
 */
export interface SearchDecoration {
  kind: "search";
  from: number;
  to: number;
  /** The match the search is on now. */
  current: boolean;
}

export type TextDecoration = SearchDecoration;

/** A stretch of text covered by the same decorations, in render order. */
export interface TextRun {
  from: number;
  to: number;
  decorations: readonly TextDecoration[];
}

interface ClampedRange {
  decoration: TextDecoration;
  from: number;
  to: number;
}

const clamp = (value: number, length: number) =>
  Math.min(Math.max(0, value), length);

/**
 * Cuts `text` at every decoration edge into runs. Each character lands in
 * exactly one run, so overlapping decorations nest rather than repeat text,
 * and a run carries every decoration that covers it. A point decoration gets
 * an empty run of its own, before the text that starts at its offset.
 */
export function buildTextRuns(
  text: string,
  decorations: readonly TextDecoration[],
): TextRun[] {
  const length = text.length;
  const ranges: ClampedRange[] = decorations
    .filter(({ from, to }) => from <= to && from <= length && to >= 0)
    .map((decoration) => ({
      decoration,
      from: clamp(decoration.from, length),
      to: clamp(decoration.to, length),
    }))
    // A range that only touches an end of the text is not a point.
    .filter(({ decoration, from, to }) => from < to || decoration.from === decoration.to);
  const spans = ranges
    .filter(({ from, to }) => from < to)
    .sort((left, right) => left.from - right.from);
  const points = new Map<number, TextDecoration[]>();
  ranges
    .filter(({ from, to }) => from === to)
    .forEach(({ decoration, from }) =>
      points.set(from, [...(points.get(from) ?? []), decoration]),
    );

  const edges = new Set<number>([0, length]);
  spans.forEach(({ from, to }) => {
    edges.add(from);
    edges.add(to);
  });
  points.forEach((_, offset) => edges.add(offset));
  const sorted = Array.from(edges).sort((left, right) => left - right);

  const runs: TextRun[] = [];
  let active: ClampedRange[] = [];
  let nextSpan = 0;
  sorted.forEach((edge, position) => {
    (points.get(edge) ?? []).forEach((decoration) =>
      runs.push({ from: edge, to: edge, decorations: [decoration] }),
    );
    const next = sorted[position + 1];
    if (next === undefined) {
      return;
    }
    active = active.filter(({ to }) => to > edge);
    while (nextSpan < spans.length && spans[nextSpan].from === edge) {
      active.push(spans[nextSpan]);
      nextSpan += 1;
    }
    runs.push({
      from: edge,
      to: next,
      decorations: active.map(({ decoration }) => decoration),
    });
  });
  return runs;
}
