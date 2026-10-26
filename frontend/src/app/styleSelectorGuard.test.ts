import postcss from "postcss";
import selectorParser from "postcss-selector-parser";
import { describe, expect, it } from "vitest";
import { styleSources } from "../test/styleRules";

// react-datepicker owns this DOM and exposes class hooks rather than data attributes.
// task-deadline-calendar is our adapter distinguishing the vendor's inline widget.
const allowedClasses = new Set([
  "dark", "settings-target", "task-deadline-calendar", "react-datepicker",
  "react-datepicker__header", "react-datepicker__time", "react-datepicker__day--selected",
  "react-datepicker__time-list-item--selected",
]);

function violations(source: string): string[] {
  const errors: string[] = [];
  postcss.parse(source).walkRules((rule) => {
    selectorParser((selectors) => {
      selectors.walk((node) => {
        if (node.type === "id") errors.push(node.toString());
        if (node.type === "class" && !allowedClasses.has(node.value)) errors.push(node.toString());
        if (node.type === "attribute" && /^(?:id|class|role|aria-label|aria-labelledby)$/.test(node.attribute)) {
          errors.push(node.toString());
        }
      });
    }).processSync(rule.selector);
  });
  return errors;
}

describe("component-owned style selectors", () => {
  it.each(Object.entries(styleSources))("keeps %s independent of application markup and utilities", (_, source) => {
    expect(violations(source)).toEqual([]);
  });

  it.each([
    "#speaker-panel", '[role="tablist"][aria-label="Meeting panels"]', '[class~="hover:bg-surface-inset"]',
    ".bg-action", ".border", ".react-datepicker__unknown", '[id^="segment-"]',
  ])("detects a reintroduced %s dependency even inside functional selectors", (selector) => {
    expect(violations(`html[data-style="depth"] :is([data-ui-surface], ${selector}) { color: red; }`))
      .not.toEqual([]);
  });

  it("allows interaction and validation states independently of accessible names", () => {
    expect(violations('[data-ui="button"]:not(:disabled, [aria-disabled="true"]):is(:active, '
      + '[aria-pressed="true"]):not([aria-invalid="true"]) { color: red; }')).toEqual([]);
  });
});
