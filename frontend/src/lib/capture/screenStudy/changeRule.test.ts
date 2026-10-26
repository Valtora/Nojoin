// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  CHANGE_THRESHOLD_LEVELS,
  ChangeRule,
  GRID_CHANNELS,
  GRID_COLUMNS,
  GRID_ROWS,
  KEEP_ALIVE_MS,
  gridChanged,
} from "./changeRule";

const flatGrid = (level: number) =>
  new Uint8Array(GRID_COLUMNS * GRID_ROWS * GRID_CHANNELS).fill(level);

const withBlock = (grid: Uint8Array, index: number, level: number) => {
  const next = grid.slice();
  next[index] = level;
  return next;
};

describe("screen study change rule", () => {
  it("keeps the first frame it sees", () => {
    expect(new ChangeRule().shouldKeep(flatGrid(40), 0)).toBe(true);
  });

  it("keeps a frame when one block moves by more than four levels", () => {
    const rule = new ChangeRule();
    const base = flatGrid(40);
    rule.kept(base, 0);

    expect(rule.shouldKeep(withBlock(base, 1_000, 40 + CHANGE_THRESHOLD_LEVELS), 1_000)).toBe(false);
    expect(rule.shouldKeep(withBlock(base, 1_000, 40 + CHANGE_THRESHOLD_LEVELS + 1), 1_000)).toBe(
      true,
    );
    expect(rule.shouldKeep(withBlock(base, 0, 40 - CHANGE_THRESHOLD_LEVELS - 1), 1_000)).toBe(true);
  });

  it("ignores small changes spread over every block", () => {
    const rule = new ChangeRule();
    rule.kept(flatGrid(100), 0);
    expect(rule.shouldKeep(flatGrid(104), 1_000)).toBe(false);
  });

  it("measures against the last frame kept, so a slow change adds up", () => {
    const rule = new ChangeRule();
    rule.kept(flatGrid(100), 0);
    const kept: boolean[] = [];
    for (let step = 1; step <= 6; step += 1) {
      kept.push(rule.shouldKeep(flatGrid(100 + step), step * 1_000));
    }
    // Five levels from the kept frame is the first that counts.
    expect(kept).toEqual([false, false, false, false, true, true]);
  });

  it("keeps a still screen once every 15 s", () => {
    const rule = new ChangeRule();
    const still = flatGrid(30);
    rule.kept(still, 0);
    expect(rule.shouldKeep(still, KEEP_ALIVE_MS - 1)).toBe(false);
    expect(rule.shouldKeep(still, KEEP_ALIVE_MS)).toBe(true);
    rule.kept(still, KEEP_ALIVE_MS);
    expect(rule.shouldKeep(still, KEEP_ALIVE_MS + 14_000)).toBe(false);
  });

  it("treats a grid of another shape as changed", () => {
    expect(gridChanged(flatGrid(10), new Uint8Array(10).fill(10))).toBe(true);
  });
});
