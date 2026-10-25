// @vitest-environment node

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { cssDeclarations } from "@/test/cssDeclarations";

/**
 * A shadow token that means "no shadow" must be a transparent zero shadow
 * (`0 0 #0000`), never `none` (#695).
 *
 * Tailwind writes shadow-*, ring-*, ring-offset-* and inset-shadow-* into one
 * box-shadow list on the same element. `none` is valid only as the whole value,
 * so a token of `none` inside that list makes the declaration invalid at
 * computed-value time and the element draws no box-shadow at all: a ring on
 * anything that also has shadow-card vanished in dark mode and under Classic.
 * The same holds for any stylesheet that lists two shadow tokens in one
 * box-shadow. `0 0 #0000` draws nothing and composes, which is why Tailwind
 * uses it for its own empty shadows.
 */
const APP = __dirname;
const SITE_STYLES = path.resolve(APP, "../../../site/src/styles");

const read = (file: string) => readFileSync(file, "utf8");

/** Stylesheets that declare the tokens, in every theme and palette block. */
const TOKEN_FILES = [
  path.join(APP, "tokens.css"),
  path.join(APP, "palettes.css"),
  path.join(APP, "styles.css"),
  path.join(SITE_STYLES, "site-tokens.css"),
];

/** Stylesheets that draw a box-shadow from a token. */
const CONSUMER_FILES = [
  path.join(APP, "globals.css"),
  path.join(APP, "styles.css"),
  path.join(APP, "driver-theme.css"),
  path.join(SITE_STYLES, "site.css"),
];

function shadowTokenNames(): Set<string> {
  const names = new Set<string>();
  // @theme maps a token to a shadow-* utility, which Tailwind composes with
  // rings. cssDeclarations skips at-rule blocks such as @theme, so read the
  // mapping directly.
  const globals = read(path.join(APP, "globals.css"));
  for (const [, name] of globals.matchAll(/--shadow-[\w-]+\s*:\s*var\(\s*--([\w-]+)/g)) {
    names.add(name);
  }
  for (const file of CONSUMER_FILES) {
    for (const rule of cssDeclarations(read(file))) {
      if (rule.property !== "box-shadow") continue;
      for (const [, name] of rule.value.matchAll(/var\(\s*--([\w-]+)/g)) names.add(name);
    }
  }
  return names;
}

describe("shadow tokens", () => {
  const names = shadowTokenNames();
  const declarations = TOKEN_FILES.flatMap((file) =>
    cssDeclarations(read(file))
      .filter((rule) => rule.property.startsWith("--") && names.has(rule.property.slice(2)))
      .map((rule) => ({ ...rule, file: path.basename(file) })),
  );

  it("finds the shadow tokens and their theme blocks (guards the parser)", () => {
    expect([...names]).toEqual(
      expect.arrayContaining(["surface-card-shadow", "float-shadow", "lift", "glow-inset"]),
    );
    const blocks = (token: string) =>
      declarations.filter((rule) => rule.property === `--${token}`).map((rule) => rule.selector);
    expect(blocks("surface-card-shadow")).toEqual(
      expect.arrayContaining([":root", ".dark", 'html[data-palette=classic]:not(.dark)']),
    );
    expect(blocks("float-shadow")).toEqual(expect.arrayContaining([":root", ".dark"]));
  });

  it("never sets one to none, which breaks a composed box-shadow", () => {
    const offenders = declarations
      .filter((rule) => /\bnone\b/i.test(rule.value))
      .map((rule) => `${rule.file} ${rule.selector} { ${rule.property}: ${rule.value} }`);

    expect(offenders).toEqual([]);
  });
});
