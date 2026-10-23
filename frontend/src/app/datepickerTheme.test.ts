import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { cssDeclarations } from "@/test/cssDeclarations";

/**
 * globals.css themes react-datepicker's time column, popover arrow and
 * keyboard day by repeating the library's own selectors, which then win on
 * order. A shorter selector loses to the library's longer one, as
 * `.react-datepicker__time` lost to `.react-datepicker__time-container
 * .react-datepicker__time` and left the column white in dark mode (#503). This
 * compares the two stylesheets, so a library upgrade that colours another part
 * of any of them fails here.
 */
const LIBRARY = path.resolve(
  __dirname,
  "../../node_modules/react-datepicker/dist/react-datepicker.css",
);
const GLOBALS = path.resolve(__dirname, "globals.css");

const THEMED_PARTS =
  /react-datepicker__time|react-datepicker-time__|react-datepicker__triangle|react-datepicker__day--keyboard-selected/;
// Coloured by the library but never on screen: v9 renders no time-name, and
// the today-button variant needs a `todayButton` Nojoin never passes.
const NOT_RENDERED = /react-datepicker__time-name|react-datepicker__time-container--with-today-button/;
const COLOUR_LITERAL = /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?)\(|\b(?:white|black|transparent)\b/i;
const THEMED_VALUE = /var\(--|^(?:transparent|inherit|currentcolor)$/i;

/** The colour a declaration sets, named by its longhand. */
function colourSlot(property: string): string {
  if (property === "background") return "background-color";
  if (property === "border") return "border-color";
  if (/^border-(?:top|right|bottom|left)$/.test(property)) return `${property}-color`;
  return property;
}

describe("react-datepicker time column, arrow and keyboard day colours", () => {
  const library = cssDeclarations(readFileSync(LIBRARY, "utf8")).filter(
    (rule) =>
      THEMED_PARTS.test(rule.selector) &&
      !NOT_RENDERED.test(rule.selector) &&
      COLOUR_LITERAL.test(rule.value),
  );
  const globals = cssDeclarations(readFileSync(GLOBALS, "utf8")).filter((rule) => rule.atRules === "");

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

  // The library styles no focused day or time, so Chrome drew its default
  // ring rather than the design system's (#623).
  it.each([".react-datepicker__day:focus-visible", ".react-datepicker__time-list-item:focus-visible"])(
    "rings %s with the focus ring token",
    (selector) => {
      const outline = globals.find((rule) => rule.selector === selector && rule.property === "outline");
      expect(outline?.value).toBe("2px solid var(--focus-ring)");
    },
  );

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
