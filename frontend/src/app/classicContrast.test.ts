// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { colour, colourValue, measurePairing, parseBlock } from "../../scripts/contrast-colour.mjs";
import { addStyleThemes } from "../../scripts/style-contrast.mjs";

const read = (name: string) => readFileSync(new URL(name, import.meta.url), "utf8");

describe("Classic facet contrast", () => {
  it("evaluates nested sRGB mixes without treating aliases in separate branches as cycles", () => {
    const tokens = { foreground: "#000", "surface-card": "#fff",
      dark: "color-mix(in srgb, var(--foreground) 84%, var(--surface-card))" };
    const result = colourValue("color-mix(in srgb, var(--dark) 67%, var(--surface-card))", tokens, {});
    expect(result[0]).toBeCloseTo(111.486);
    expect(result[3]).toBe(1);
    expect(() => colour("missing", tokens, {})).toThrow("missing token");
    expect(() => colour("cycle", { cycle: "var(--cycle)" }, {})).toThrow("circular");
    expect(() => colourValue("color-mix(in srgb, #0000 67%, #fff)", {}, {})).toThrow("opaque");
    expect(() => colourValue("color-mix(in srgb, #000 101%, #fff)", {}, {})).toThrow("mix weight");
    expect(() => colourValue("color-mix(in srgb, #000 ..%, #fff)", {}, {})).toThrow("mix weight");
    expect(() => colourValue("var(--missing, #fff)", {}, {})).toThrow("cannot parse colour");
    expect(() => colourValue("not-a-colour", {}, {})).toThrow("cannot parse colour");
  });

  it("measures the actual light shaded and dark lit facets across every palette", () => {
    const css = read("./tokens.css");
    const root = parseBlock(css, ":root");
    const paletteCss = read("./palettes.css");
    for (const palette of ["default", "graphite", "classic", "ultraviolet", "marigold"]) {
      for (const mode of ["light", "dark"]) {
        const name = palette === "default" ? mode : `palette-${palette}-${mode}`;
        const tokens = { ...(mode === "dark" ? parseBlock(css, ".dark") : root),
          ...(palette === "default" ? {} : parseBlock(paletteCss,
            `html[data-palette="${palette}"]${mode === "dark" ? ".dark" : ":not(.dark)"}`)) };
        const themes = [{ name, tokens, base: root, floats: true, pairings: [] }];
        addStyleThemes(themes, read("./styles.css"), parseBlock);
        const classic = themes.find((entry) => entry.name.endsWith("-style-classic"))!;
        for (const pair of classic.pairings as { fg: string; bg: string[]; min: number }[]) {
          if (pair.fg !== "style-focus-edge") {
            expect(pair.fg).toBe(mode === "dark" ? "classic-outer-highlight" : "classic-outer-dark");
          }
          expect(measurePairing(pair, classic.tokens, classic.base).ratio).toBeGreaterThanOrEqual(pair.min);
        }
      }
    }
  });

  it.each([
    ["--classic-inner-dark: color-mix(in srgb, #808080 67%, var(--surface-card));",
      "--classic-inner-dark: broken;", "cannot parse colour"],
    ["--classic-outer-dark: color-mix(in srgb, #000000 67%, var(--surface-card));",
      "--classic-outer-dark: broken;", "cannot parse colour"],
    ["--classic-inner-highlight: #dfdfdf;", "--classic-inner-highlight: var(--classic-inner-highlight);", "circular"],
    ["--classic-inner-highlight: #dfdfdf;", "", "missing token"],
  ])("rejects invalid rendered facets even when their colour is not an identifying edge", (original, changed, error) => {
    const css = read("./tokens.css");
    const root = parseBlock(css, ":root");
    const themes = [
      { name: "light", tokens: root, base: root, floats: true, pairings: [] },
      { name: "dark", tokens: parseBlock(css, ".dark"), base: root, floats: true, pairings: [] },
    ];
    expect(() => addStyleThemes(themes, read("./styles.css").replace(original, changed), parseBlock)).toThrow(error);
  });
});
