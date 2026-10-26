import { readFileSync } from "node:fs";
import postcss, { type Rule } from "postcss";
import { selectorFor } from "./depthSelectors";

const read = (file: string) => readFileSync(new URL(file, import.meta.url), "utf8");
export const styleSources = Object.fromEntries(
  ["styles", "classic", "depth-dark", "depth-gradient"].map((name) => [
    name, read(`../app/${name}.css`),
  ]),
);

/** Inspect actual declarations, keeping nesting and media conditions available to contracts. */
export function styleRules(sheet: string, property: string, value?: string): Rule[] {
  const result: Rule[] = [];
  postcss.parse(styleSources[sheet]).walkRules((rule) => {
    if (rule.nodes.some((node) => node.type === "decl" && node.prop === property
      && (value === undefined || node.value === value))) result.push(rule);
  });
  return result;
}

/** jsdom has no pointer state; replace only the explicitly simulated pseudo-classes. */
export function stateSelector(rule: Rule, state: { hover?: boolean; active?: boolean } = {}): string {
  let selector = selectorFor(rule);
  for (const [name, enabled] of Object.entries(state)) {
    selector = selector.replaceAll(`:${name}`, enabled ? ":where(*)" : ":not(*)");
  }
  return selector;
}

export function mediaConditions(rule: Rule): string[] {
  const media: string[] = [];
  let node: postcss.Node | undefined = rule.parent;
  while (node) {
    if (node.type === "atrule") {
      const atRule = node as postcss.AtRule;
      if (atRule.name === "media") media.push(atRule.params);
    }
    node = node.parent;
  }
  return media;
}
