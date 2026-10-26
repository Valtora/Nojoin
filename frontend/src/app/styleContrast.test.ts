// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { composite, flattenStack, measurePairing, parseBlock, parseColour } from "../../scripts/contrast-colour.mjs";
import { addStyleThemes } from "../../scripts/style-contrast.mjs";

describe("contrast colour parsing", () => {
  it.each([
    ["#abc", [170, 187, 204, 1]],
    ["#1234", [17, 34, 51, 68 / 255]],
    ["#112233", [17, 34, 51, 1]],
    ["#11223380", [17, 34, 51, 128 / 255]],
    ["rgb(15 23 42 / 0.18)", [15, 23, 42, 0.18]],
    ["rgba(15, 23, 42, .18)", [15, 23, 42, 0.18]],
  ])("parses supported colour %s", (value, expected) => {
    expect(parseColour(value as string, "edge")).toEqual(expected);
  });

  it("composites translucent background layers and text over the actual opaque backdrop", () => {
    expect(composite([255, 255, 255, .08], [40, 40, 40, 1])).toEqual([57.2, 57.2, 57.2, 1]);
    const tokens = { page: "#000", panel: "rgb(255 255 255 / .5)", text: "rgb(255 255 255 / .5)" };
    const measured = measurePairing({ fg: "text", bg: ["page", "panel"] }, tokens, {});
    expect(measured.bg).toEqual([127.5, 127.5, 127.5, 1]);
    expect(measured.fg).toEqual([191.25, 191.25, 191.25, 1]);
    expect(() => flattenStack(["panel"], tokens, {})).toThrow("must be opaque");
    expect(() => flattenStack([], tokens, {})).toThrow("empty background");
  });

  it.each([
    "#12345", "#1234567", "rgb(100% 0% 0%)", "rgb(15 23 42 / 20%)",
    "rgb(1 2)", "rgb(1 2 3 4)", "rgb(1,,3)", "rgb(1,2,3 / .5)",
    "rgb(1 2 300)", "rgba(1, 2, 3, 2)", "rgb(NaN 0 0)", "rgb(1 2 3 / .5 / .5)",
  ])("rejects unsupported or malformed colour %s instead of passing NaN", (value) => {
    expect(() => parseColour(value, "edge")).toThrow("cannot parse colour");
  });
});

describe("style contrast cascade", () => {
  const css = 'html[data-style="classic"]:not(.dark) { }\nhtml[data-style="depth"]:not(.dark) { }';
  const theme = (name: string, floats = true) => ({
    name, floats, tokens: { face: "palette", lightOnly: "base", foreground: "#000", "surface-card": "#fff",
      "control-border": "#888", "focus-ring": "#000", "style-focus-edge": "#000",
      "classic-outer-highlight": "#fff", "classic-inner-highlight": "#ddd",
      "classic-outer-dark": "#222", "classic-inner-dark": "#444" }, base: {}, pairings: [],
  });
  const block = (_css: string, selector: string) => {
    if (selector.endsWith("]")) return { shared: "shared-style", face: "shared-face" };
    return selector.endsWith(".dark")
      ? { face: "dark-style" }
      : { face: "light-style", lightOnly: "light-style-only" };
  };

  it("overlays every app palette and mode, preserving base fallbacks and site themes", () => {
    const themes = [theme("light"), theme("dark"), theme("palette-graphite-dark"), theme("site-dark", false)];
    addStyleThemes(themes, css, block);
    expect(themes).toHaveLength(10);
    expect(themes.find((entry) => entry.name === "dark-style-classic")?.tokens)
      .toMatchObject({ face: "dark-style", lightOnly: "base", shared: "shared-style" });
    expect(themes.find((entry) => entry.name === "light-style-depth")?.tokens.face).toBe("light-style");
    expect(themes.find((entry) => entry.name === "palette-graphite-dark-style-depth")?.pairings)
      .toEqual(expect.arrayContaining([expect.objectContaining({ fg: "style-focus-edge", min: 3 })]));
    expect(themes.filter((entry) => entry.name.startsWith("site-"))).toHaveLength(1);
  });

  it("requires both new styles and both mode token blocks", () => {
    expect(() => addStyleThemes([], "", block)).toThrow("missing classic");
    const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
    const themes = [theme("light"), theme("dark")];
    addStyleThemes(themes, styles, parseBlock);
    expect(themes).toHaveLength(6);
    expect(() => addStyleThemes([], styles.replace('html[data-style="depth"].dark', ".missing"), parseBlock))
      .toThrow('selector html[data-style="depth"].dark not found');
    expect(() => parseBlock('html[data-style="classic"] { --edge: #fff;', 'html[data-style="classic"]'))
      .toThrow("unterminated selector");
  });
});
