import { lineFollowedAt, segmentStartMs, shownLineAt, type PlacementSegment } from "./placement";

/**
 * A place a point note can be moved to: after the shown line `afterIndex`,
 * by anchoring the note at that line's start.
 */
export interface MoveTarget {
  /** The list index of the shown line the note will follow. */
  afterIndex: number;
  anchorMs: number;
}

/**
 * Where a point note can be moved among the lines a view shows: the gap after
 * each shown line `i`, with `anchor_ms = start_ms(i)`, which placement then
 * puts after `i`.
 *
 * Only gaps that placement can reach are offered. One a later line would take
 * instead is left out: a line sharing its start with a later one, a blank
 * line, which is never an anchor, or one a later-listed line starts before.
 * The gap before every line is not offered, so a note always moves after a
 * line.
 *
 * `visibleIndexes` are the list indexes of the shown lines, ascending, as for
 * `bodyNotesAmongLines`.
 */
export function moveTargets(
  segments: readonly PlacementSegment[],
  visibleIndexes: readonly number[],
): MoveTarget[] {
  const followed = lineFollowedAt(segments);
  const targets: MoveTarget[] = [];
  for (const index of visibleIndexes) {
    const anchorMs = segmentStartMs(segments[index]);
    if (anchorMs < 0) continue;
    if (shownLineAt(visibleIndexes, followed(anchorMs)) === index) {
      targets.push({ afterIndex: index, anchorMs });
    }
  }
  return targets;
}

/**
 * The target a step up (-1) or down (1) reaches from a note shown after line
 * `shownAfter` (-1 before every line): the nearest gap above or below its
 * own, or null at either end.
 */
export function targetBeside(
  targets: readonly MoveTarget[],
  shownAfter: number,
  direction: -1 | 1,
): MoveTarget | null {
  if (direction > 0) return targets.find((target) => target.afterIndex > shownAfter) ?? null;
  let above: MoveTarget | null = null;
  for (const target of targets) {
    if (target.afterIndex >= shownAfter) break;
    above = target;
  }
  return above;
}
