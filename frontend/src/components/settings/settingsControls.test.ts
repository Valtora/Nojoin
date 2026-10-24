// @vitest-environment node

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  SETTINGS_BUTTON_DANGER,
  SETTINGS_BUTTON_PRIMARY,
  SETTINGS_BUTTON_SECONDARY,
} from "./settingsControls";

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
  // A button busy with its own request is aria-disabled rather than disabled,
  // so it keeps keyboard focus (#614), and has to look the same.
  it.each([
    ["primary", SETTINGS_BUTTON_PRIMARY],
    ["secondary", SETTINGS_BUTTON_SECONDARY],
    ["danger", SETTINGS_BUTTON_DANGER],
  ])("dims a %s button when aria-disabled, as when disabled", (_, classes) => {
    expect(classes).toContain("disabled:opacity-60");
    expect(classes).toContain("aria-disabled:opacity-60");
    expect(classes).toContain("aria-disabled:cursor-not-allowed");
  });

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
