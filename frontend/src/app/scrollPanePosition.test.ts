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
 *
 * Stylesheets follow the same rule: a rule that sets `overflow` to `auto` or
 * `scroll` also sets `position`, in the same rule (#1002).
 */
const SRC = path.resolve(__dirname, "..");

const SCROLLS = /(?:^|[\s"'`])(?:[^\s"'`]*:)?overflow(?:-[xy])?-(?:auto|scroll)(?=$|[\s"'`])/;
const POSITIONED = /(?:^|[\s"'`])(?:relative|absolute|fixed|sticky)(?=$|[\s"'`])/;
const COMMENT = /^\s*(?:\/\/|\/\*|\*|\{\/\*)/;
// A textarea scrolls its own text and has no child elements to contain. It is
// recognised by resize-none, which only textareas use here.
const TEXTAREA = /\bresize-none\b/;

function filesUnder(dir: string, keep: (name: string) => boolean): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return filesUnder(full, keep);
    return keep(name) ? [full] : [];
  });
}

const sourceFiles = (dir: string) =>
  filesUnder(dir, (name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name));

function unpositionedScrollers(source: string): number[] {
  return source.split("\n").flatMap((line, index) =>
    SCROLLS.test(line) && !POSITIONED.test(line) && !COMMENT.test(line) && !TEXTAREA.test(line)
      ? [index + 1]
      : [],
  );
}

const STYLE_SCROLLS = /(?:^|[;\s])overflow(?:-[xy])?\s*:[^;]*\b(?:auto|scroll)\b/;
const STYLE_POSITIONED = /(?:^|[;\s])position\s*:\s*(?:relative|absolute|fixed|sticky)\b/;
// Stylesheet rules that scroll without positioning their element, and why that
// is safe. Each must still name a rule that scrolls.
const STYLE_EXEMPT = new Set([
  // react-datepicker's own stylesheet makes .react-datepicker relative.
  ".react-datepicker-popper .react-datepicker",
  // It holds a table whose cells are all relative, so nothing inside reaches past a cell.
  ".ProseMirror .tableWrapper",
]);

/**
 * Each rule's selector and its own declarations, for rules nested in at-rules
 * or in other rules too. A brace walk rather than a CSS parser: comments are
 * blanked first, and no string in these stylesheets holds a brace.
 */
function stylesheetRules(css: string): { selector: string; body: string }[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, " ");
  const rules: { selector: string; body: string }[] = [];
  const open: { selector: string; body: string }[] = [];
  let start = 0;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char !== "{" && char !== "}" && char !== ";") continue;
    const chunk = text.slice(start, index);
    start = index + 1;
    if (char === "{") {
      open.push({ selector: chunk.trim().replace(/\s+/g, " "), body: "" });
      continue;
    }
    const rule = open.at(-1);
    if (!rule) continue;
    rule.body += `${chunk};`;
    if (char === "}") {
      open.pop();
      rules.push(rule);
    }
  }
  return rules;
}

const scrollingRules = (css: string) =>
  stylesheetRules(css).filter((rule) => STYLE_SCROLLS.test(rule.body));

function unpositionedStylesheetScrollers(css: string): string[] {
  return scrollingRules(css)
    .filter((rule) => !STYLE_POSITIONED.test(rule.body) && !STYLE_EXEMPT.has(rule.selector))
    .map((rule) => rule.selector);
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

  it("recognises a stylesheet rule that scrolls and is not positioned", () => {
    // .page-shell as #1002 found it, and as it was fixed.
    expect(unpositionedStylesheetScrollers(".page-shell { height: 100dvh; overflow-y: auto; }")).toEqual([
      ".page-shell",
    ]);
    expect(
      unpositionedStylesheetScrollers(".page-shell { position: relative; height: 100dvh; overflow-y: auto; }"),
    ).toEqual([]);
    expect(unpositionedStylesheetScrollers("@layer components { .a { overflow: hidden scroll } }")).toEqual([".a"]);
    expect(unpositionedStylesheetScrollers(".a { color: red; &:hover { overflow-x: auto } }")).toEqual(["&:hover"]);
    expect(unpositionedStylesheetScrollers(".a { overflow: auto; .b { color: red } position: sticky; }")).toEqual([]);
    expect(unpositionedStylesheetScrollers("/* .a { overflow: auto } */ .b { color: red }")).toEqual([]);
    expect(
      unpositionedStylesheetScrollers(".a { overflow: hidden; text-overflow: ellipsis; overflow-wrap: anywhere }"),
    ).toEqual([]);
  });

  it("positions every stylesheet rule that scrolls", () => {
    const stylesheets = filesUnder(SRC, (name) => name.endsWith(".css"));
    const css = stylesheets.map((file) => readFileSync(file, "utf8"));
    const offenders = stylesheets.flatMap((file, index) =>
      unpositionedStylesheetScrollers(css[index]).map((selector) => `${path.relative(SRC, file)}: ${selector}`),
    );
    expect(offenders).toEqual([]);

    const scrolling = new Set(css.flatMap((sheet) => scrollingRules(sheet).map((rule) => rule.selector)));
    expect([...STYLE_EXEMPT].filter((selector) => !scrolling.has(selector))).toEqual([]);
  });
});
