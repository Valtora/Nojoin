import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Source guard for the settings control vocabulary (settingsControls.ts).
 *
 * Settings drifted once: sections wrote their own control classes, focus rings
 * drawn in the fill colour rather than --focus-ring, and fields filled with the
 * translucent inset, which DESIGN.md forbids on a control because it leaks into
 * a native select's popup. Both are cheap to write again and look fine in light
 * mode, so they are caught here.
 */
const DIR = __dirname;

function settingsFiles(): string[] {
  return readdirSync(DIR).filter(
    (name) => name.endsWith(".tsx") && !/\.test\.tsx?$/.test(name),
  );
}

function violations(pattern: RegExp): string[] {
  return settingsFiles().flatMap((file) =>
    readFileSync(path.join(DIR, file), "utf8")
      .split("\n")
      .flatMap((line, index) => (pattern.test(line) ? [`${file}:${index + 1}`] : [])),
  );
}

describe("settings controls", () => {
  it("draws focus with the shared ring, not a hand-written one", () => {
    expect(violations(/\bfocus(-visible)?:ring-/)).toEqual([]);
  });

  it("never fills an input, select or textarea with the translucent inset", () => {
    // The whole opening tag, across lines; `=>` inside a handler is not its end.
    const tag = /<(input|select|textarea)\b(?:[^>]|=>)*?bg-surface-inset(?:[^>]|=>)*>/g;
    const found = settingsFiles().flatMap((file) =>
      [...readFileSync(path.join(DIR, file), "utf8").matchAll(tag)].map(
        (match) => `${file}: ${match[0].slice(0, 60)}`,
      ),
    );
    expect(found).toEqual([]);
  });

  it("colours every divide-y with a token", () => {
    // `divide-y` borders the children, so `settings-divider` on the parent
    // never reaches them and the rules fall back to the text colour.
    expect(violations(/^(?!.*\bdivide-(surface|control)-).*\bdivide-y\b/)).toEqual([]);
  });
});
