// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { colour, measurePairing, parseBlock } from "../../scripts/contrast-colour.mjs";
import { addDepthGradientThemes, addDepthThemes } from "../../scripts/depth-contrast.mjs";
import { addStyleThemes } from "../../scripts/style-contrast.mjs";

const read = (file: string) => readFileSync(new URL(file, import.meta.url), "utf8");
const css = read("./depth-dark.css");
const root = parseBlock(read("./tokens.css"), ":root");
const dark = parseBlock(read("./tokens.css"), ".dark");
type Pair = { label: string; fg: string; bg: string[]; min: number };
type Theme = { name: string; tokens: Record<string, string>; base: Record<string, string>;
  floats: boolean; pairings: Pair[] };

function themes(source = css): Theme[] {
  const entries: Theme[] = ["default", "graphite", "classic", "ultraviolet", "marigold"].map((palette) => ({
    name: palette === "default" ? "dark" : `palette-${palette}-dark`,
    tokens: { ...dark, ...(palette === "default" ? {} : parseBlock(read("./palettes.css"),
      `html[data-palette="${palette}"].dark`)) }, base: root, floats: true, pairings: [],
  }));
  addStyleThemes(entries, read("./styles.css"), parseBlock);
  addDepthThemes(entries, source);
  addDepthGradientThemes(entries, read("./depth-gradient.css"));
  return entries.filter((entry) => entry.name.endsWith("-style-depth"));
}

describe("dark Depth's independent resting perimeter", () => {
  it("clears 3:1 on neutral, nested, translucent, tinted and gradient hosts in every palette", () => {
    const entries = themes();
    expect(entries).toHaveLength(5);
    for (const entry of entries) {
      expect(colour("depth-filled-edge", entry.tokens, entry.base))
        .not.toEqual(colour("focus-ring", entry.tokens, entry.base));
      const pairs = entry.pairings.filter((pair) => pair.label.startsWith("depth filled perimeter"));
      expect(pairs.length).toBeGreaterThan(100);
      for (const pair of pairs) {
        expect(pair.min).toBe(3);
        expect(measurePairing(pair, entry.tokens, entry.base).ratio, `${entry.name}: ${pair.label}`)
          .toBeGreaterThanOrEqual(3);
      }
    }
  });

  it("rejects a focus alias even when its numerical contrast would pass", () => {
    expect(() => themes(css.replace(/--depth-filled-edge: [^;]+;/, "--depth-filled-edge: var(--focus-ring);")))
      .toThrow("independent of keyboard focus");
  });

  it("detects a perimeter that disappears into a real surrounding surface", () => {
    const broken = themes(css.replace(/--depth-filled-edge: [^;]+;/,
      "--depth-filled-edge: var(--surface-page);"));
    expect(broken.every((entry) => entry.pairings.some((pair) => pair.fg === "depth-filled-edge"
      && measurePairing(pair, entry.tokens, entry.base).ratio < pair.min))).toBe(true);
  });
});
