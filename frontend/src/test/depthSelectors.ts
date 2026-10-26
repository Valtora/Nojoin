import postcss, { type Rule } from "postcss";

/** Expand nesting for selector matching only; deliberately no cascade resolution. */
export function selectorFor(rule: Rule): string {
  const parent = rule.parent;
  const ancestor = parent?.type === "rule" ? parent : parent?.parent;
  return ancestor?.type === "rule"
    ? rule.selector.replaceAll("&", `:is(${selectorFor(ancestor)})`) : rule.selector;
}

export function paintSelectors(source: string): string[] {
  const selectors: string[] = [];
  postcss.parse(source).walkRules((rule) => {
    if (rule.nodes.some((node) => node.type === "decl" && node.prop === "background-image"
      && node.value !== "none")) selectors.push(selectorFor(rule));
  });
  return selectors;
}

/** Synthetic colour/cascade matrix; owner contracts are exercised by rendered component tests. */
export function depthFixture(dark = false, style = "depth") {
  const fixture = document.implementation.createHTMLDocument();
  fixture.documentElement.dataset.style = style;
  fixture.documentElement.className = dark ? "dark" : "";
  fixture.body.innerHTML = '<div id="speaker-panel" data-ui-reading="pane" data-ui-fill="inset"></div>'
    + '<div class="react-datepicker" id="calendar"><div class="react-datepicker__time-container">'
    + '<div class="react-datepicker__time" id="time"></div></div></div>'
    + '<div class="react-datepicker task-deadline-calendar" id="inline-calendar"></div>'
    + '<div data-ui-resize-group="vertical"><button data-ui-side-tab="my-notes" aria-selected="false"></button>'
    + '<div data-ui-resize-handle="vertical"><div data-ui-resize-grip></div></div></div>';
  return fixture;
}
