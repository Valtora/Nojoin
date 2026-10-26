// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import postcss, { type Rule } from "postcss";
import { describe, expect, it } from "vitest";
import { depthFixture, selectorFor } from "../test/depthSelectors";

const read = (file: string) => readFileSync(new URL(file, import.meta.url), "utf8");
const gradient = read("./depth-gradient.css");

describe("Depth settings search landing cue selectors", () => {
  it("removes gradient paint on marked raised hosts in both modes, including panes", () => {
    const rules: Rule[] = [];
    postcss.parse(gradient).walkRules((rule) => {
      if (rule.selector.includes(".settings-target")) rules.push(rule);
    });
    expect(rules).toHaveLength(3);
    for (const dark of [false, true]) {
      const fixture = depthFixture(dark);
      for (const role of ["card", "pane", "float"]) {
        const target = fixture.createElement("section");
        target.dataset.uiSurface = role;
        target.className = "settings-card settings-target bg-surface-card";
        fixture.body.append(target);
        const matching = rules.filter((rule) => target.matches(selectorFor(rule)));
        expect(matching.length).toBeGreaterThan(0);
        for (const rule of matching) {
          expect(rule.nodes.some((node) => node.type === "decl" && node.prop === "background-image"
            && node.value === "none")).toBe(true);
        }
        const main = matching.find((rule) => rule.nodes.some((node) => node.type === "decl"
          && node.prop === "animation-fill-mode"));
        expect(main).toBeDefined();
        expect(main?.nodes.some((node) => node.type === "decl" && node.prop === "animation-fill-mode"
          && node.value === "none")).toBe(true);
        const reduced = main?.nodes.find((node) => node.type === "atrule"
          && node.params === "(prefers-reduced-motion: reduce)");
        expect(reduced?.type === "atrule" && reduced.nodes?.some((node) => node.type === "decl"
          && node.prop === "background-color" && node.value === "var(--settings-tab-active-bg)")).toBe(true);
        expect(reduced?.type === "atrule" && reduced.nodes?.some((node) => node.type === "decl"
          && node.prop === "animation" && node.value === "none")).toBe(true);
        target.classList.remove("settings-target");
        expect(rules.some((rule) => target.matches(selectorFor(rule)))).toBe(false);
      }
    }
  });
});
