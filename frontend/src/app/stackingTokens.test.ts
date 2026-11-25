// @vitest-environment node

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { cssDeclarations } from "@/test/cssDeclarations";

/**
 * The stacking ladder in tokens.css. Every overlay takes its z-index from it,
 * so the order of these numbers is the order things cover each other in.
 */
const z = (() => {
  const css = readFileSync(path.join(__dirname, "tokens.css"), "utf8");
  const values = new Map<string, number>();
  for (const { property, value, atRules } of cssDeclarations(css)) {
    if (atRules === "" && property.startsWith("--z-")) {
      values.set(property.slice(4), Number(value));
    }
  }
  return (name: string) => {
    const value = values.get(name);
    if (value === undefined) throw new Error(`--z-${name} is not declared`);
    return value;
  };
})();

describe("stacking tokens", () => {
  it("keeps sticky chrome under dropdowns, and dropdowns under dialogs", () => {
    expect(z("sticky")).toBeLessThan(z("dropdown"));
    expect(z("dropdown")).toBeLessThan(z("modal"));
  });

  it("draws a toast over a dialog but under a context menu or popover (#974)", () => {
    // A dialog's own error shows over it, and a toast never hides the last
    // items of a row menu opened near the foot of a phone's screen.
    expect(z("toast")).toBeGreaterThan(z("modal"));
    expect(z("toast")).toBeLessThan(z("popover"));
  });

  it("keeps the tour above everything, driver.js's overlay at 10000 included", () => {
    expect(z("tour")).toBeGreaterThan(10000);
    for (const name of ["sticky", "dropdown", "modal", "toast", "popover"]) {
      expect(z(name)).toBeLessThan(z("tour"));
    }
  });
});
