import { readFileSync } from "node:fs";

import postcss from "postcss";

import { selectorFor } from "./depthSelectors";

type StyleSource = "styles.css" | "depth-dark.css" | "depth-gradient.css";

/** Match a real rendered owner against rules that own the asserted declaration. */
export function matchesStyleDeclaration(
  element: Element,
  source: StyleSource,
  property: string,
  value: string,
  { style = "depth", dark = false }: { style?: string; dark?: boolean } = {},
): boolean {
  const root = element.ownerDocument.documentElement;
  const previousStyle = root.getAttribute("data-style");
  const previousDark = root.classList.contains("dark");
  root.dataset.style = style;
  root.classList.toggle("dark", dark);

  try {
    const selectors: string[] = [];
    const sourcePath = `../app/${source}`;
    postcss.parse(readFileSync(new URL(sourcePath, import.meta.url), "utf8")).walkRules((rule) => {
      if (rule.nodes.some((node) => node.type === "decl" && node.prop === property && node.value === value)) {
        selectors.push(selectorFor(rule));
      }
    });
    return selectors.some((selector) => element.matches(selector));
  } finally {
    if (previousStyle === null) root.removeAttribute("data-style");
    else root.setAttribute("data-style", previousStyle);
    root.classList.toggle("dark", previousDark);
  }
}
