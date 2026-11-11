// @vitest-environment node

import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Source guard for scrolling panes (follow-up to #996).
 *
 * An absolutely positioned element resolves against its nearest positioned
 * ancestor. A pane that scrolls without being positioned neither clips nor
 * scrolls the absolute elements inside it, screen-reader-only text among them:
 * they stay at their unscrolled place in whatever positioned box lies outside,
 * usually <main>, and give that box a scroll range of its own. In the Settings
 * column that let the page scroll on past its last card (#992).
 *
 * So a class string that makes an element scroll also positions it: `relative`,
 * or `absolute`, `fixed` or `sticky` where the element already is one of those.
 * The check is per line, so keep the position class on the line that scrolls.
 */
const SRC = path.resolve(__dirname, "..");

const SCROLLS = /(?:^|[\s"'`])(?:[^\s"'`]*:)?overflow(?:-[xy])?-(?:auto|scroll)(?=$|[\s"'`])/;
const POSITIONED = /(?:^|[\s"'`])(?:relative|absolute|fixed|sticky)(?=$|[\s"'`])/;
const COMMENT = /^\s*(?:\/\/|\/\*|\*|\{\/\*)/;
// A textarea scrolls its own text and has no child elements to contain. It is
// recognised by resize-none, which only textareas use here.
const TEXTAREA = /\bresize-none\b/;

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [full] : [];
  });
}

function unpositionedScrollers(source: string): number[] {
  return source.split("\n").flatMap((line, index) =>
    SCROLLS.test(line) && !POSITIONED.test(line) && !COMMENT.test(line) && !TEXTAREA.test(line)
      ? [index + 1]
      : [],
  );
}

describe("scrolling panes", () => {
  it("recognises a scrolling class string that is not positioned", () => {
    // The Settings column as #992 found it, and as #996 fixed it.
    expect(unpositionedScrollers('<div className="min-h-0 flex-1 overflow-y-auto">')).toEqual([1]);
    expect(unpositionedScrollers('<div className="relative min-h-0 flex-1 overflow-y-auto">')).toEqual([]);
    expect(unpositionedScrollers('className={`flex ${open ? "md:overflow-x-auto" : ""}`}')).toEqual([1]);
    expect(unpositionedScrollers('className="md:relative overflow-auto"')).toEqual([1]);
    expect(unpositionedScrollers('className="absolute inset-0 overflow-y-scroll"')).toEqual([]);
    expect(unpositionedScrollers('className="overflow-x-hidden overflow-y-clip"')).toEqual([]);
  });

  it("positions every pane that scrolls, so absolute descendants stay inside it", () => {
    const offenders = sourceFiles(SRC).flatMap((file) =>
      unpositionedScrollers(readFileSync(file, "utf8")).map(
        (line) => `${path.relative(SRC, file)}:${line}`,
      ),
    );
    expect(offenders).toEqual([]);
  });
});
