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

/** Fill-owner markup from SpeakerPanel, react-datepicker and RecordingSideColumn. */
export function depthFixture(dark = false, style = "depth") {
  const fixture = document.implementation.createHTMLDocument();
  fixture.documentElement.dataset.style = style;
  fixture.documentElement.className = dark ? "dark" : "";
  fixture.body.innerHTML = '<div id="speaker-panel" class="bg-surface-inset"></div>'
    + '<div class="react-datepicker" id="calendar"><div class="react-datepicker__time-container">'
    + '<div class="react-datepicker__time" id="time"></div></div></div>'
    + '<div class="react-datepicker task-deadline-calendar" id="inline-calendar"></div>'
    + '<div data-panel-group-direction="vertical"><button id="side-tab-my-notes" aria-selected="false"></button>'
    + '<div data-panel-resize-handle-id="fixture" data-panel-group-direction="vertical" class="bg-surface-inset"><div></div></div></div>';
  return fixture;
}
