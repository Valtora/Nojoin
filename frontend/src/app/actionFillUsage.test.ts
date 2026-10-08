import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Source guard: anything drawn on an action fill (bg-action, or its
 * bg-action-hover and bg-action-active states) takes text-action-on (or
 * text-action-on-muted).
 *
 * The contrast gate proves action-on against action in every palette, but it
 * cannot see which text token a component actually puts on the fill. The
 * fill's label is not always white: Classic and Marigold invert their dark
 * fill to a light colour with a dark label, so foreground, white or a muted
 * grey on bg-action can drop to ~1.3:1 there while looking fine in the default
 * palette.
 *
 * A heuristic over source lines, not a parser. It checks the common text
 * colours (foreground, white, black, and the contrast-, action-, rail- and
 * status- families) in the class string carrying the fill, and on any element
 * opened on the next three lines (an icon or label nested in it). Only
 * unprefixed utilities count, so hover:bg-action over a resting text colour is
 * fine; a quoted class string is cut at its closing quote, so a ternary's other
 * branch is ignored; a nested element that sets its own surface (any other
 * bg-*) ends the window; and a self-closing element ("/>") has no children,
 * so nothing after it is inside the fill.
 */
const SRC = path.resolve(__dirname, "..");
const UNPREFIXED = "(?:^|[\\s\"'`{(])!?";
const ACTION_FILL_NAME = "bg-action(?:-hover|-active)?(?![-\\w])";
const ACTION_FILL = new RegExp(`${UNPREFIXED}${ACTION_FILL_NAME}`);
const TEXT_COLOUR = new RegExp(
  `${UNPREFIXED}text-(?!action-on\\b|action-on-muted\\b)(foreground|white|black|contrast-[\\w-]+|action-[\\w-]+|rail-[\\w-]+|status-[\\w-]+)\\b`,
);
const OTHER_SURFACE = new RegExp(
  `${UNPREFIXED}bg-(?!action(?:-hover|-active)?(?![-\\w]))[\\w-]+`,
);

/** The quoted class string that contains the fill, so a ternary's other branch on the same line is ignored. */
function actionClassString(line: string): string {
  const start = line.search(ACTION_FILL);
  const rest = line.slice(start + 1);
  const end = rest.search(/["'`]/);
  return end === -1 ? line.slice(start) : line.slice(start, start + 1 + end);
}

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [full] : [];
  });
}

function findViolations(): string[] {
  const found: string[] = [];
  for (const file of sourceFiles(SRC)) {
    const lines = readFileSync(file, "utf8").split("\n");
    lines.forEach((line, index) => {
      if (!ACTION_FILL.test(line)) return;
      const lastOffset = /\/>\s*$/.test(line) ? 0 : 3;
      for (let offset = 0; offset <= lastOffset && index + offset < lines.length; offset += 1) {
        const candidate = offset === 0 ? actionClassString(line) : lines[index + offset];
        if (offset > 0 && !/<[A-Za-z]/.test(candidate)) continue;
        if (offset > 0 && OTHER_SURFACE.test(candidate)) break;
        if (TEXT_COLOUR.test(candidate)) {
          found.push(`${path.relative(SRC, file)}:${index + offset + 1}`);
        }
      }
    });
  }
  return found;
}

describe("action fill usage", () => {
  it("labels everything on bg-action with text-action-on", () => {
    expect(findViolations()).toEqual([]);
  });
});
