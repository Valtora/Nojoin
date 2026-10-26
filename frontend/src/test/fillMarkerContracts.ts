import { render, type RenderOptions } from "@testing-library/react";
import type { ReactNode } from "react";

export const fillBackgrounds: Record<string, string> = {
  card: "bg-surface-card",
  inset: "bg-surface-inset",
  "inset-translucent": "bg-surface-inset/80",
  float: "bg-surface-float",
  control: "bg-control-bg",
  "control-disabled": "bg-control-disabled-bg",
  rail: "bg-rail",
  action: "bg-action",
  danger: "bg-danger",
  "action-tint": "bg-action-tint",
  ...Object.fromEntries(["neutral", "info", "success", "warning", "danger"]
    .map((tone) => [`status-${tone}`, `bg-status-${tone}-bg`])),
};

export const fillAliases: Record<string, string> = {
  "settings-card": "card",
  "settings-inset": "inset",
};

const backgroundFills = new Map(Object.entries(fillBackgrounds).map(([fill, background]) => [background, fill]));
const owners = new Set(["surface", "button", "input", "select", "textarea", "switch", "modal"]);

/** Compare local base fills; state-prefixed utilities and decorative descendants are independent. */
export function fillMarkerViolations(root: ParentNode): string[] {
  const elements = [...root.querySelectorAll("[data-ui-fill], [data-ui], [data-ui-reading]")];
  if (root instanceof Element && root.matches("[data-ui-fill], [data-ui], [data-ui-reading]")) {
    elements.unshift(root);
  }
  return elements.flatMap((element) => {
    if (element.hasAttribute("data-ui-tooltip") || element.getAttribute("data-ui") === "badge") return [];
    const marker = element.getAttribute("data-ui-fill");
    // Range tracks follow the native-control contract rather than field background treatments.
    const nativeRange = element.getAttribute("data-ui") === "input" && element.getAttribute("type") === "range";
    // Reading bubbles own speaker-colour utilities rather than the neutral fill recipe contract.
    const semanticOwner = !nativeRange && (owners.has(element.getAttribute("data-ui") ?? "")
      || element.getAttribute("data-ui-reading") === "pane");
    const backgrounds = [...element.classList].filter((token) => backgroundFills.has(token));
    // These aliases own their base colours in globals.css; their declarations are checked separately.
    for (const [alias, fill] of Object.entries(fillAliases)) {
      if (element.classList.contains(alias)) backgrounds.push(fillBackgrounds[fill]);
    }
    const fills = [...new Set(backgrounds.map((background) => backgroundFills.get(background)))];
    const label = element.id ? `#${element.id}` : element.tagName.toLowerCase();
    if (marker === null) {
      return semanticOwner && fills.length ? [`${label}: missing data-ui-fill for ${backgrounds.join(" ")}`] : [];
    }
    if (!Object.hasOwn(fillBackgrounds, marker)) return [`${label}: unknown data-ui-fill=${marker}`];
    if (!fills.length) return [`${label}: data-ui-fill=${marker} is missing ${fillBackgrounds[marker]}`];
    return fills.length === 1 && fills[0] === marker ? []
      : [`${label}: data-ui-fill=${marker} disagrees with ${backgrounds.join(" ")}`];
  });
}

/** Keep initial renders and subsequent variant transitions under the same contract. */
export function renderWithFillContract(ui: ReactNode, options?: RenderOptions) {
  const result = render(ui, options);
  const check = () => {
    const violations = fillMarkerViolations(result.baseElement);
    if (violations.length) throw new Error(violations.join("\n"));
  };
  check();
  return {
    ...result,
    rerender(next: ReactNode) {
      result.rerender(next);
      check();
    },
  };
}
