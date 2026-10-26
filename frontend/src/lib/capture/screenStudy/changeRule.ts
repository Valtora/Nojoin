/**
 * When the screen study keeps a frame (#653).
 *
 * Each sample is reduced to a 64 x 36 grid of block averages of red, green and
 * blue. A frame is kept when any channel of any block differs from the last
 * frame kept by more than four levels, or when 15 s have passed since it, so a
 * still screen still says what it showed. Comparing with the last frame kept,
 * not the last one looked at, lets a slow change add up until it counts.
 *
 * Each channel is compared rather than brightness, because a meeting app's
 * ring around the speaking tile is coloured and thin: lit on a still 4K screen
 * at 100% or 125% scaling, a 2 px ring moved a block's brightness by only three
 * or four levels, but its strongest channel by six or more. A grey change, such
 * as a text cursor blinking, moves every channel as it moves brightness, so it
 * counts exactly as it did before. A finer grid would have caught the ring too,
 * but it would also have kept a frame for most cursor blinks.
 */

export const GRID_COLUMNS = 64;

export const GRID_ROWS = 36;

/** Red, green and blue, in that order within each block. */
export const GRID_CHANNELS = 3;

export const CHANGE_THRESHOLD_LEVELS = 4;

export const KEEP_ALIVE_MS = 15_000;

export const gridChanged = (
  previous: Uint8Array,
  next: Uint8Array,
  threshold = CHANGE_THRESHOLD_LEVELS,
): boolean => {
  if (previous.length !== next.length) {
    return true;
  }
  for (let index = 0; index < next.length; index += 1) {
    if (Math.abs(next[index] - previous[index]) > threshold) {
      return true;
    }
  }
  return false;
};

export class ChangeRule {
  private lastKeptGrid: Uint8Array | null = null;

  private lastKeptAt: number | null = null;

  /** Whether to keep a sample with this grid, taken at `now` (ms). */
  shouldKeep(grid: Uint8Array, now: number): boolean {
    if (!this.lastKeptGrid || this.lastKeptAt === null) {
      return true;
    }
    return now - this.lastKeptAt >= KEEP_ALIVE_MS || gridChanged(this.lastKeptGrid, grid);
  }

  kept(grid: Uint8Array, now: number) {
    this.lastKeptGrid = grid;
    this.lastKeptAt = now;
  }
}
