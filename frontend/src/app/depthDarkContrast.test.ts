// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import postcss from "postcss";
import selectorParser from "postcss-selector-parser";
import { describe, expect, it } from "vitest";
import { colour, composite, measurePairing, mixOklab, mixPercent, parseBlock } from "../../scripts/contrast-colour.mjs";
import { addDepthGradientThemes, addDepthThemes, splitterPairings } from "../../scripts/depth-contrast.mjs";
import { addStyleThemes } from "../../scripts/style-contrast.mjs";
import { depthFixture, paintSelectors, selectorFor } from "../test/depthSelectors";
import { gradientStops, paintColour } from "../../scripts/depth-paint.mjs";

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
        { label: "rail border vs rail", fg: "rail-border", bg: ["rail-bg"], min: 1.15 },
        { label: "chart series 1 vs card", fg: "chart-1", bg: ["surface-page", "surface-card"], min: 1.15 },
        { label: "speaker red vs card", fg: "speaker-red", bg: ["surface-page", "surface-card"], min: 1.15 },
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
  fixture.body.innerHTML = ["button", "a", 'div data-ui="button"']
    .flatMap((tag) => ["action", "danger"].map((fill) =>
      `<${tag} data-ui-fill="${fill}" id="${tag.split(" ")[0]}-${fill}"></${tag.split(" ")[0]}>`)).join("")
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
    expect(perimeterTargets(css.replace(':is([data-ui-fill="action"], [data-ui-fill="danger"])', ".missing-filled")))
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
  const fixture = depthFixture(darkMode, style);
  fixture.body.insertAdjacentHTML("beforeend", '<section data-ui-surface="pane" data-ui-fill="card" id="pane"></section>'
    + '<section data-ui-surface="float" data-ui-fill="float" id="float"></section>'
    + '<section data-ui-surface="card" data-ui-fill="card" id="card"></section>'
    + '<section data-ui-surface="pane" id="unfilled"></section>'
    + '<div data-ui-surface="inset" data-ui-fill="card" id="bubble"></div>'
    + '<input data-ui-input="field" data-ui-surface="pane" data-ui-fill="control" id="field">'
    + '<button data-ui-chrome="action" data-ui-surface="card" data-ui-fill="card" id="neutral"></button>'
    + '<button data-ui-chrome="action" data-ui-fill="action" id="primary"></button>'
    + '<button data-ui-chrome="action" data-ui-fill="danger" id="danger"></button>'
    + '<section data-ui-surface="pane" data-ui-fill="status-info" id="status"></section>'
    + '<div data-ui-surface="float" data-ui-tooltip data-ui-fill="card" id="tooltip"></div>');
  const selectors = paintSelectors(source);
  return [...fixture.querySelectorAll("[id]")]
    .filter((element) => selectors.some((selector) => element.matches(selector))).map((element) => element.id);
}

describe("Depth production gradients", () => {
  it("paints filled raised roles automatically, excluding fields, bubbles and semantic fills in both modes", () => {
    for (const darkMode of [false, true]) {
      expect(gradientTargets(gradientCss, "depth", darkMode)).toEqual(["speaker-panel", "calendar", "time",
        "pane", "float", "card", ...(darkMode ? ["primary"] : [])]);
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
  });

  it("fails measurements when production safeguards, endpoints or the host's inset shade change", () => {
    for (const source of [gradientCss.replace("--control-border: var(--contrast-icon-muted);", ""),
      gradientCss.replace("--depth-light-pane-inset: 7.5%;", "--depth-light-pane-inset: 10%;"),
      gradientCss.replace("var(--depth-light-bottom) 100%", "var(--foreground) 100%"),
      gradientCss.replace("--depth-light-shade: var(--depth-light-pane-inset)", "--depth-light-shade: var(--depth-light-pane)")]) {
      const entries = gradientThemes(source).filter((entry) => entry.name.endsWith("-style-depth"));
      expect(entries.some((entry) => entry.pairings.some((pair) =>
        measurePairing(pair, entry.tokens, entry.base).ratio < pair.min))).toBe(true);
    }
  });

  it("rejects disconnected special production hosts and retains the light paint guard", () => {
    for (const host of ['[data-ui-reading="pane"]', ".react-datepicker", ".react-datepicker__time"]) {
      const broken = gradientCss.replaceAll(host, `.missing-${host.replace(/[^a-z]/g, "")}`);
      expect(gradientTargets(broken, "depth", false)).not.toEqual(gradientTargets(gradientCss, "depth", false));
    }
    expect(gradientTargets(gradientCss.replaceAll('html[data-style="depth"]:not(.dark)', "html"), "classic", false))
      .not.toEqual([]);
  });

  it("preserves the house floor for flat rails, charts and speaker colours", () => {
    for (const [foreground, background] of [["rail-border", "rail-bg"], ["chart-1", "surface-card"], ["speaker-red", "surface-card"]]) {
      const entries = themes();
      for (const entry of entries.filter((item) => item.name.endsWith("-style-depth"))) entry.tokens[foreground] = `var(--${background})`;
      addDepthThemes(entries, css);
      addDepthGradientThemes(entries, gradientCss);
      expect(entries.some((entry) => entry.pairings.some((pair) => pair.fg === foreground
        && measurePairing(pair, entry.tokens, entry.base).ratio < pair.min))).toBe(true);
    }
  });

  it("measures splitter strip and grip values in every state", () => {
    const entries = themes();
    addDepthThemes(entries, css);
    for (const entry of entries.filter((item) => item.name.endsWith("-style-depth"))) {
      const pairs = splitterPairings(entry, gradientCss);
      expect(pairs).toHaveLength(8);
      expect(pairs.every((pair: Pairing) => measurePairing(pair, entry.tokens, entry.base).ratio >= pair.min)).toBe(true);
    }
  });

  // CSS Selectors 4 specificity as [ids, classes, types]; :is/:not/:has take their most specific argument.
  const specificity = (node: selectorParser.Container | selectorParser.Node): number[] => {
    const total = [0, 0, 0];
    const add = (s: number[]) => s.forEach((value, index) => { total[index] += value; });
    const max = (list: selectorParser.Container) => list.nodes.map(specificity)
      .reduce((a, b) => (compare(a, b) >= 0 ? a : b), [0, 0, 0]);
    if (node.type === "id") total[0] += 1;
    else if (node.type === "class" || node.type === "attribute") total[1] += 1;
    else if (node.type === "tag") total[2] += 1;
    else if (node.type === "pseudo") {
      if ([":is", ":not", ":has"].includes(node.value)) add(max(node));
      else if (node.value !== ":where") total[node.value.startsWith("::") ? 2 : 1] += 1;
      return total;
    }
    if ("nodes" in node && node.type === "selector") node.nodes.forEach((child) => add(specificity(child)));
    return total;
  };
  const selectorSpecificity = (selector: string) => {
    let result = [0, 0, 0];
    selectorParser((root) => { result = specificity(root.nodes[0]); }).processSync(selector);
    return result;
  };
  const compare = (a: number[], b: number[]) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];

  it("keeps raised gradient paint above the dark fill reset after removing ID specificity", () => {
    const fixture = depthFixture(true);
    for (const role of ["card", "pane", "float"]) {
      const host = fixture.createElement("section");
      host.dataset.uiSurface = role;
      host.dataset.uiFill = "card";
      fixture.body.append(host);
      const selectors = (source: string, paint: boolean) => {
        const result: string[] = [];
        postcss.parse(source).walkRules((rule) => {
          if (rule.nodes.some((node) => node.type === "decl" && node.prop === "background-image"
            && (node.value !== "none") === paint) && host.matches(selectorFor(rule))) {
            result.push(selectorFor(rule));
          }
        });
        return result;
      };
      const resets = selectors(css, false), paints = selectors(gradientCss, true);
      expect(resets.length).toBeGreaterThan(0);
      expect(paints.length).toBeGreaterThan(0);
      for (const reset of resets) expect(paints.some((paint) =>
        compare(selectorSpecificity(paint), selectorSpecificity(reset)) >= 0)).toBe(true);
    }
  });

  it("keeps the strip hover specificity tie and matches both resize orientations", () => {
    const tree = postcss.parse(gradientCss);
    const strip = tree.nodes.find((node) => node.type === "rule"
      && node.selector.includes("[data-ui-resize-handle]") && node.selector.endsWith(":hover"));
    if (strip?.type !== "rule") throw new Error("missing strip hover rule");
    // Hover must win over the vertical base fill: equal or higher specificity, later in source.
    const bases = tree.nodes.filter((node): node is postcss.Rule => node.type === "rule"
      && node.selector.includes('[data-ui-resize-handle="vertical"]') && !node.selector.includes(":has("));
    expect(bases.length).toBeGreaterThan(0);
    for (const base of bases) {
      expect(compare(selectorSpecificity(strip.selector), selectorSpecificity(base.selector))).toBeGreaterThanOrEqual(0);
      expect(tree.nodes.indexOf(strip)).toBeGreaterThan(tree.nodes.indexOf(base));
    }
    for (const darkMode of [false, true]) {
      const fixture = depthFixture(darkMode);
      const handle = fixture.querySelector("[data-ui-resize-handle]")!;
      for (const direction of ["vertical", "horizontal"]) {
        handle.setAttribute("data-ui-resize-handle", direction);
        expect(handle.matches(strip.selector.replace(":hover", ""))).toBe(true);
      }
      const grip = tree.nodes.find((node) => node.type === "rule"
        && node.selector.includes("[data-ui-resize-handle]:hover > [data-ui-resize-grip]"));
      expect(grip?.type === "rule" && handle.firstElementChild!.matches(grip.selector.replace(":hover", ""))).toBe(true);
    }
  });

  it("reads real gradient stop declarations and rejects unsupported paint rather than assuming the recipe", () => {
    expect(gradientStops("linear-gradient(in oklab, #fff 0%, #000 100%)", {}).map((stop: { position: number }) => stop.position))
      .toEqual([0, 1]);
    expect(() => gradientStops("radial-gradient(#fff, #000)", {})).toThrow("unsupported");
    expect(() => paintColour("color-mix(in unknown, #fff 50%, #000)")).toThrow("cannot parse");
  });
});
