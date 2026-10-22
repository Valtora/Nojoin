import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * globals.css themes react-datepicker's time column and popover arrow by
 * repeating the library's own selectors, which then win on order. A shorter
 * selector loses to the library's longer one, as `.react-datepicker__time` lost
 * to `.react-datepicker__time-container .react-datepicker__time` and left the
 * column white in dark mode (#503). This compares the two stylesheets, so a
 * library upgrade that colours another part of either fails here.
 */
const LIBRARY = path.resolve(
  __dirname,
  "../../node_modules/react-datepicker/dist/react-datepicker.css",
);
const GLOBALS = path.resolve(__dirname, "globals.css");

const THEMED_PARTS = /react-datepicker__time|react-datepicker-time__|react-datepicker__triangle/;
// Coloured by the library but never on screen: v9 renders no time-name, and
// the today-button variant needs a `todayButton` Nojoin never passes.
const NOT_RENDERED = /react-datepicker__time-name|react-datepicker__time-container--with-today-button/;
const COLOUR_LITERAL = /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?)\(|\b(?:white|black|transparent)\b/i;
const THEMED_VALUE = /var\(--|^(?:transparent|inherit|currentcolor)$/i;

type Declaration = { selector: string; property: string; value: string; atRules: string; order: number };

/** Splits on a separator outside parentheses, so `:not(a, b)` stays whole. */
function splitTopLevel(text: string, separator: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = "";
  for (const char of text) {
    if (char === "(") depth += 1;
    if (char === ")") depth -= 1;
    if (char === separator && depth === 0) {
      parts.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  parts.push(current);
  return parts.map((part) => part.trim()).filter(Boolean);
}

const normaliseSelector = (selector: string) =>
  selector.replace(/\s+/g, " ").replace(/(\[[^\]=]+[\^$*|~]?=)["']([^"']*)["']\]/g, "$1$2]").trim();

/** The colour a declaration sets, named by its longhand. */
function colourSlot(property: string): string {
  if (property === "background") return "background-color";
  if (property === "border") return "border-color";
  if (/^border-(?:top|right|bottom|left)$/.test(property)) return `${property}-color`;
  return property;
}

/** Every declaration in the sheet, one per selector of a group, with its at-rule context. */
function declarations(css: string): Declaration[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const found: Declaration[] = [];
  const preludes: string[] = [];
  let buffer = "";
  let order = 0;
  for (const char of text) {
    if (char === "{") {
      preludes.push(buffer.trim());
      buffer = "";
    } else if (char === "}") {
      const prelude = preludes.pop() ?? "";
      if (prelude && !prelude.startsWith("@")) {
        const atRules = preludes.filter((outer) => outer.startsWith("@")).join(" ");
        for (const declaration of splitTopLevel(buffer, ";")) {
          const colon = declaration.indexOf(":");
          if (colon < 0) continue;
          const property = declaration.slice(0, colon).trim().toLowerCase();
          const value = declaration.slice(colon + 1).trim();
          for (const selector of splitTopLevel(prelude, ",")) {
            found.push({ selector: normaliseSelector(selector), property, value, atRules, order });
          }
        }
        order += 1;
      }
      buffer = "";
    } else {
      buffer += char;
    }
  }
  return found;
}

describe("react-datepicker time column and arrow colours", () => {
  const library = declarations(readFileSync(LIBRARY, "utf8")).filter(
    (rule) =>
      THEMED_PARTS.test(rule.selector) &&
      !NOT_RENDERED.test(rule.selector) &&
      COLOUR_LITERAL.test(rule.value),
  );
  const globals = declarations(readFileSync(GLOBALS, "utf8")).filter((rule) => rule.atRules === "");

  it("finds the library's colours (guards the parser)", () => {
    expect(library.length).toBeGreaterThan(10);
    expect(library.map((rule) => rule.selector)).toContain(
      ".react-datepicker__time-container .react-datepicker__time",
    );
  });

  it("repeats each one through the library's selector with a token", () => {
    const missing = library
      .filter(
        (rule) =>
          !globals.some(
            (override) =>
              override.selector === rule.selector &&
              colourSlot(override.property) === colourSlot(rule.property) &&
              THEMED_VALUE.test(override.value),
          ),
      )
      .map((rule) => `${rule.selector} { ${rule.property}: ${rule.value} }`);

    expect(missing).toEqual([]);
  });

  it("puts each time state after the plain hover, which also matches it", () => {
    const item =
      ".react-datepicker__time-container .react-datepicker__time .react-datepicker__time-box ul.react-datepicker__time-list li.react-datepicker__time-list-item";
    const orderOf = (suffix: string) =>
      globals.find((rule) => rule.selector === `${item}${suffix}`)?.order ?? -1;

    const hover = orderOf(":hover");
    expect(hover).toBeGreaterThanOrEqual(0);
    for (const state of ["--selected", "--selected:hover", "--disabled", "--disabled:hover"]) {
      expect(orderOf(state), state).toBeGreaterThan(hover);
    }
  });

  it("colours the column without !important", () => {
    const forced = globals.filter(
      (rule) =>
        THEMED_PARTS.test(rule.selector) &&
        /color|background|fill|stroke/.test(rule.property) &&
        rule.value.includes("!important"),
    );
    expect(forced).toEqual([]);
  });
});
