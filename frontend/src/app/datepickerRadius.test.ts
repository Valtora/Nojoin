import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * datepicker-radius.css re-declares every fixed radius in react-datepicker's
 * stylesheet through --radius-scale, so the calendar follows the Corner style
 * setting. A library upgrade that adds or moves a radius would silently opt
 * that part of the calendar out again; this compares the two stylesheets.
 */
const LIBRARY = path.resolve(
  __dirname,
  "../../node_modules/react-datepicker/dist/react-datepicker.css",
);
const OVERRIDES = path.resolve(__dirname, "datepicker-radius.css");

type RadiusRule = { selector: string; property: string; value: string };

function radiusRules(css: string): RadiusRule[] {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const rules: RadiusRule[] = [];
  for (const [, rawSelector, body] of withoutComments.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    const selector = rawSelector.replace(/\s+/g, " ").replace(/\s*,\s*/g, ",").trim();
    for (const declaration of body.split(";")) {
      const [property, value] = declaration.split(":").map((part) => part?.trim());
      if (!property?.includes("radius") || !value) continue;
      rules.push({ selector, property, value });
    }
  }
  return rules;
}

/** A radius the corner scale should own: any non-zero length that is not a percentage circle. */
function isScalable(value: string): boolean {
  return !value.includes("%") && value.split(/\s+/).some((part) => part !== "0");
}

describe("react-datepicker corner radii", () => {
  const library = radiusRules(readFileSync(LIBRARY, "utf8")).filter((rule) =>
    isScalable(rule.value),
  );
  const overrides = radiusRules(readFileSync(OVERRIDES, "utf8"));

  it("finds the library's radii (guards the parser)", () => {
    expect(library.length).toBeGreaterThan(10);
  });

  it("scales every fixed radius in the library stylesheet", () => {
    const missing = library
      .filter(
        (rule) =>
          !overrides.some(
            (override) =>
              override.selector === rule.selector &&
              override.property === rule.property &&
              override.value.includes("var(--radius-scale)"),
          ),
      )
      .map((rule) => `${rule.selector} { ${rule.property}: ${rule.value} }`);

    expect(missing).toEqual([]);
  });
});
