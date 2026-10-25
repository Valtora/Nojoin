// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import postcss from "postcss";
import { describe, expect, it } from "vitest";
import { colour, composite, measurePairing, mixOklab, mixPercent, parseBlock } from "../../scripts/contrast-colour.mjs";
import { addDepthGradientThemes, addDepthThemes } from "../../scripts/depth-contrast.mjs";
import { addStyleThemes } from "../../scripts/style-contrast.mjs";

type Pairing = { label: string; fg: string; bg: string[]; min: number };
type Theme = { name: string; tokens: Record<string, string>; base: Record<string, string>;
  floats: boolean; pairings: Pairing[] };
const read = (file: string) => readFileSync(new URL(file, import.meta.url), "utf8");
const css = read("./depth-dark.css");
const gradientCss = read("./depth-gradient.css");
const root = parseBlock(read("./tokens.css"), ":root");
const dark = parseBlock(read("./tokens.css"), ".dark");

function themes(): Theme[] {
  const result: Theme[] = ["default", "graphite", "classic", "ultraviolet", "marigold"]
    .flatMap((palette) => ["light", "dark"].map((mode) => ({
      name: palette === "default" ? mode : `palette-${palette}-${mode}`,
      tokens: { ...(mode === "dark" ? dark : root), ...(palette === "default" ? {} : parseBlock(
        read("./palettes.css"), `html[data-palette="${palette}"]${mode === "dark" ? ".dark" : ":not(.dark)"}`)) },
      base: root, floats: true,
      pairings: [
        { label: "body", fg: "foreground", bg: ["surface-page", "surface-card"], min: 4.5 },
        { label: "primary label", fg: "action-on", bg: ["action"], min: 4.5 },
        { label: "primary boundary", fg: "action", bg: ["surface-page", "surface-card"], min: 3 },
      ],
    })));
  result.push({ name: "site-dark", tokens: dark, base: root, floats: false, pairings: [] });
  addStyleThemes(result, read("./styles.css"), parseBlock);
  return result;
}

function perimeterTargets(source: string, style = "depth", darkMode = true): string[] {
  const selectors: string[] = [];
  postcss.parse(source).walkRules((rule) => {
    if (rule.nodes.some((node) => node.type === "decl" && node.prop === "box-shadow"
      && /^inset\s+0\s+0\s+0\s+1px\s+var\(--depth-filled-edge\),/.test(node.value))) {
      selectors.push(rule.selector);
    }
  });
  const fixture = document.implementation.createHTMLDocument();
  fixture.documentElement.dataset.style = style;
  fixture.documentElement.className = darkMode ? "dark" : "";
  fixture.body.innerHTML = ["button", "a", "div role=button"]
    .flatMap((tag) => ["action", "danger"].map((fill) =>
      `<${tag} class="bg-${fill}" id="${tag.split(" ")[0]}-${fill}"></${tag.split(" ")[0]}>`)).join("")
    + '<button class="bg-surface-card" id="neutral"></button>';
  return [...fixture.querySelectorAll("[id]")]
    .filter((element) => selectors.some((selector) => element.matches(selector))).map((element) => element.id);
}

describe("Depth effective backgrounds", () => {
  it("interpolates Oklab, premultiplies alpha and agrees with independent Chromium pixels", () => {
    mixOklab([0, 0, 0, 1], [255, 255, 255, 1], .5).slice(0, 3)
      .forEach((channel: number) => expect(channel).toBeCloseTo(99.086, 2));
    const uv = { ...dark, ...parseBlock(read("./palettes.css"), 'html[data-palette="ultraviolet"].dark') };
    for (const [fraction, expected] of [[.03, [41, 34, 61]], [.06, [47, 41, 67]], [.09, [54, 47, 73]]] as const) {
      const mix = mixOklab(colour("surface-inset", uv, root), colour("foreground", uv, root), fraction);
      expect(mix[3]).toBeCloseTo(.035 * (1 - fraction) + fraction, 10);
      expect(composite(mix, colour("surface-card", uv, root)).slice(0, 3).map(Math.round)).toEqual(expected);
    }
    const transparent = mixOklab([255, 0, 0, 0], [0, 0, 255, 1], .5);
    expect(transparent.slice(0, 3).map(Math.round)).toEqual([0, 0, 255]);
    expect(transparent[3]).toBe(.5);
    expect(mixOklab([255, 0, 0, 0], [0, 255, 0, 0], .5)).toEqual([0, 0, 0, 0]);
  });

  it("measures role/fill stacks in every palette while preserving other styles and original thresholds", () => {
    const entries = themes();
    const untouched = structuredClone(entries.filter((entry) => !entry.name.endsWith("-style-depth")));
    addDepthThemes(entries, css);
    expect(entries.filter((entry) => !entry.name.endsWith("-style-depth"))).toEqual(untouched);
    for (const entry of entries.filter((item) => item.name.endsWith("-style-depth"))) {
      expect(entry.pairings.find((pair) => pair.label === "primary label"))
        .toEqual({ label: "primary label", fg: "action-on", bg: ["action"], min: 4.5 });
      for (const pair of entry.pairings) {
        expect(measurePairing(pair, entry.tokens, entry.base).ratio).toBeGreaterThanOrEqual(pair.min);
      }
    }
    const entry = entries.find((item) => item.name === "dark-style-depth")!;
    expect(entry.pairings.find((pair) => pair.label === "depth chat placeholder control-placeholder")?.bg)
      .toEqual(["depth-pane-surface-card", "depth-inset-surface-inset"]);
    expect(entry.pairings.find((pair) => pair.label === "depth float card-fill foreground")?.bg)
      .toEqual(["depth-float-surface-card"]);
    expect(entry.pairings.find((pair) => pair.label === "depth merge field foreground")?.bg)
      .toEqual(["depth-float-surface-float", "action-tint", "depth-inset-control-bg"]);
    expect(entry.pairings.find((pair) => pair.label === "primary boundary")?.fg).toBe("depth-filled-edge");
  });

  it("fails the CLI measurement threshold when a lighter actual background crosses the boundary", () => {
    const entries = themes();
    const original = entries.find((entry) => entry.name === "dark-style-depth")!;
    original.tokens.foreground = "#878787";
    expect(measurePairing(original.pairings[0], original.tokens, original.base).ratio).toBeGreaterThan(4.5);
    addDepthThemes(entries, css);
    const pair = original.pairings.find((item) => item.label === "depth resting: body")!;
    expect(measurePairing(pair, original.tokens, original.base).ratio).toBeLessThan(pair.min);
  });

  it("mixes the translucent inset/80 fill with the configured inset treatment", () => {
    const entries = themes();
    addDepthThemes(entries, css.replace("--depth-mix-inset: 0%;", "--depth-mix-inset: 5%;"));
    const entry = entries.find((item) => item.name === "dark-style-depth")!;
    const input = colour("surface-inset", dark, root);
    input[3] *= .8;
    const expected = mixOklab(input, colour("foreground", dark, root), .05);
    colour("depth-inset-surface-inset-80", entry.tokens, entry.base).forEach((channel: number, index: number) => {
      expect(channel).toBeCloseTo(expected[index], 10);
    });
  });

  it("rejects missing settings, malformed percentages, unsupported colours and alias cycles", () => {
    expect(() => addDepthThemes([], "")).toThrow("not found");
    expect(() => addDepthThemes([], css.replace("--depth-mix-card: 3%;", "--depth-mix-card: NaN%;")))
      .toThrow("invalid percentage");
    expect(() => mixPercent("101%", "weight")).toThrow("out-of-range");
    expect(() => colour("a", { a: "var(--b)", b: "var(--a)" }, {})).toThrow("circular");
    expect(() => colour("a", {}, {})).toThrow("missing");
    expect(() => colour("a", { a: "color-mix(in unknown, #fff, #000)" }, {})).toThrow("cannot parse");
    expect(() => mixOklab([0, 0, 0, 1], [255, 255, 255, 1], Number.NaN)).toThrow("invalid");
  });

  it("attaches the identifying perimeter rule to both real filled-control selectors in dark Depth", () => {
    expect(perimeterTargets(css)).toEqual(["button-action", "button-danger", "a-action", "a-danger",
      "div-action", "div-danger"]);
    expect(perimeterTargets(css, "classic")).toEqual([]);
    expect(perimeterTargets(css, "depth", false)).toEqual([]);
    expect(perimeterTargets(css.replace(":is(.bg-action, .bg-danger)", ":is(.missing-action, .missing-danger)")))
      .toEqual([]);
  });
});

function gradientThemes(source = gradientCss): Theme[] {
  const entries = themes();
  addDepthThemes(entries, css);
  addDepthGradientThemes(entries, source);
  return entries;
}

function gradientTargets(source: string, style: string, darkMode: boolean): string[] {
  const fixture = document.implementation.createHTMLDocument();
  fixture.documentElement.dataset.style = style;
  fixture.documentElement.className = darkMode ? "dark" : "";
  fixture.body.innerHTML = '<section data-ui-surface="pane" class="bg-surface-card" id="pane"></section>'
    + '<section data-ui-surface="float" class="bg-surface-float" id="float"></section>'
    + '<section data-ui-surface="card" class="bg-surface-card" id="card"></section>'
    + '<section data-ui-surface="pane" id="unfilled"></section>'
    + '<div data-ui-surface="inset" class="bg-surface-card" id="bubble"></div>'
    + '<input data-ui-input="field" data-ui-surface="pane" class="bg-control-bg" id="field">'
    + '<button data-ui-chrome="action" data-ui-surface="card" class="bg-surface-card" id="neutral"></button>'
    + '<button data-ui-chrome="action" class="bg-action" id="primary"></button>'
    + '<button data-ui-chrome="action" class="bg-danger" id="danger"></button>'
    + '<section data-ui-surface="pane" class="bg-status-info-bg" id="status"></section>'
    + '<div data-ui-surface="float" role="tooltip" class="bg-surface-card" id="tooltip"></div>'
    + '<div class="react-datepicker task-deadline-calendar" id="inline-calendar"></div>';
  const selectors: string[] = [];
  postcss.parse(source).walkRules((rule) => {
    if (rule.parent?.type === "root" && rule.nodes.some((node) => node.type === "decl"
      && node.prop === "background-image" && /gradient\(|var\(--depth-(?:light-neutral|gradient)-paint\)/.test(node.value))) {
      selectors.push(rule.selector);
    }
  });
  return [...fixture.querySelectorAll("[id]")].filter((element) =>
    selectors.some((selector) => element.matches(selector))).map((element) => element.id);
}

describe("Depth production gradients", () => {
  it("paints filled raised roles automatically, excluding fields, bubbles and semantic fills in both modes", () => {
    for (const darkMode of [false, true]) {
      expect(gradientTargets(gradientCss, "depth", darkMode)).toEqual(["pane", "float", "card", ...(darkMode ? ["primary"] : [])]);
      expect(gradientTargets(gradientCss, "classic", darkMode)).toEqual([]);
      expect(gradientTargets(gradientCss, "default", darkMode)).toEqual([]);
    }
  });

  it("measures nested independent stops, flat fields, primary states and splitter identification in every palette", () => {
    const entries = gradientThemes();
    const flat = themes();
    addDepthThemes(flat, css);
    expect(entries.filter((entry) => !entry.name.endsWith("-style-depth")))
      .toEqual(flat.filter((entry) => !entry.name.endsWith("-style-depth")));
    for (const entry of entries.filter((item) => item.name.endsWith("-style-depth"))) {
      for (const pair of entry.pairings) {
        expect(measurePairing(pair, entry.tokens, entry.base).ratio, `${entry.name}: ${pair.label}`)
          .toBeGreaterThanOrEqual(pair.min);
      }
    }
    const light = entries.find((entry) => entry.name === "light-style-depth")!;
    const nested = light.pairings.find((pair) => pair.label === "depth gradient chat pane foreground [gradient 28]")!;
    expect(nested.bg).toEqual(["depth-gradient-pane-surface-card-4", "depth-gradient-pane-surface-inset-4"]);
    const placeholder = light.pairings.find((pair) => pair.label === "depth gradient chat placeholder control-placeholder [gradient 4]")!;
    expect(placeholder.bg.at(-1)).toBe("surface-inset");
    expect(light.pairings.find((pair) => pair.label === "primary label [gradient 0]")?.bg).toEqual(["action"]);
  });

  it("fails real contrast measurements when a necessary safeguard or the lighter nested stop is removed", () => {
    for (const source of [gradientCss.replace("--control-border: var(--contrast-icon-muted);", ""),
      gradientCss.replace("--depth-light-pane-inset: 7.5%;", "--depth-light-pane-inset: 10%;")]) {
      const entries = gradientThemes(source).filter((entry) => entry.name.endsWith("-style-depth"));
      expect(entries.some((entry) => entry.pairings.some((pair) =>
        measurePairing(pair, entry.tokens, entry.base).ratio < pair.min))).toBe(true);
    }
  });
});
