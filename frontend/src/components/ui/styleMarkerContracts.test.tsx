import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { paintSelectors, selectorFor } from "@/test/depthSelectors";
import { mediaConditions, stateSelector, styleRules, styleSources } from "@/test/styleRules";
import { renderWithFillContract as render } from "@/test/fillMarkerContracts";
import Button from "./Button";
import IconButton from "./IconButton";
import Card from "./Card";
import Input from "./Input";
import Select from "./Select";
import Modal from "./Modal";
import Tooltip from "./Tooltip";
import SettingsAdvanced from "../settings/SettingsAdvanced";
import SettingsCard from "../settings/SettingsCard";
import SettingsCallout from "../settings/SettingsCallout";

afterEach(() => {
  document.documentElement.removeAttribute("data-style");
  document.documentElement.classList.remove("dark");
});

const depth = () => {
  document.documentElement.dataset.style = "depth";
  document.documentElement.classList.add("dark");
};
const matchesPaint = (element: Element) => paintSelectors(styleSources["depth-gradient"])
  .some((selector) => element.matches(selector));
const perimeter = () => styleRules("depth-dark", "box-shadow")[0];

describe("real primitive style recipes", () => {
  it.each(["primary", "secondary", "ghost", "danger"] as const)(
    "connects the %s Button recipe to filled perimeters and neutral state fills", (variant) => {
      depth();
      render(<Button variant={variant}>Save</Button>);
      const button = screen.getByRole("button", { name: "Save" });
      expect(button.matches(selectorFor(perimeter()))).toBe(["primary", "danger"].includes(variant));
      const neutral = styleRules("depth-dark", "--depth-fill-base", "var(--surface-inset)")
        .find((rule) => rule.selector.includes('[data-ui-hover="inset"]'))!;
      expect(button.matches(stateSelector(neutral, { hover: true }))).toBe(variant === "secondary");
      expect(mediaConditions(neutral)).toContain("(hover: hover)");
      expect(button).toHaveAttribute("data-ui-border", "full");
    },
  );

  it("keeps transparent danger IconButton distinct from filled danger Button", () => {
    depth();
    render(<IconButton variant="danger" aria-label="Delete" icon={<span />} />);
    const button = screen.getByRole("button", { name: "Delete" });
    expect(button).not.toHaveAttribute("data-ui-fill");
    expect(button.matches(selectorFor(perimeter()))).toBe(false);
  });

  it.each(["primary", "secondary", "ghost"] as const)(
    "connects the independent %s IconButton recipe to its actual paint and state selectors", (variant) => {
      depth();
      render(<IconButton variant={variant} aria-label="Action" icon={<span />} />);
      const button = screen.getByRole("button", { name: "Action" });
      expect(button.matches(selectorFor(perimeter()))).toBe(variant === "primary");
      const neutral = styleRules("depth-dark", "--depth-fill-base", "var(--surface-inset)")
        .find((rule) => rule.selector.includes('[data-ui-hover="inset"]'))!;
      expect(button.matches(stateSelector(neutral, { hover: true }))).toBe(variant === "secondary");
      const primaryHover = styleRules("depth-gradient", "--depth-gradient-action", "var(--action-hover)")[0];
      expect(button.matches(stateSelector(primaryHover, { hover: true }))).toBe(variant === "primary");
      expect(button).toHaveAttribute("data-ui-chrome", variant === "ghost" ? "plain" : "action");
    },
  );

  it("connects primary hover/active recipes while excluding disabled, loading and pressed gradient paint", () => {
    depth();
    const { rerender } = render(<Button variant="primary">Save</Button>);
    const button = screen.getByRole("button", { name: "Save" });
    const hover = styleRules("depth-gradient", "--depth-gradient-action", "var(--action-hover)")[0];
    const active = styleRules("depth-gradient", "--depth-gradient-action", "var(--action-active)")[0];
    expect(button.matches(stateSelector(hover, { hover: true }))).toBe(true);
    expect(button.matches(stateSelector(active, { active: true }))).toBe(true);
    expect(mediaConditions(hover)).toContain("(hover: hover)");
    rerender(<Button variant="primary" disabled>Save</Button>);
    expect(matchesPaint(button)).toBe(false);
    expect(button.matches(selectorFor(perimeter()))).toBe(true);
    rerender(<Button variant="primary" loading>Save</Button>);
    expect(button).toBeEnabled();
    expect(matchesPaint(button)).toBe(false);
    rerender(<Button variant="primary" aria-pressed="true">Save</Button>);
    const pressed = styleRules("depth-gradient", "background-image", "none")
      .find((rule) => rule.selector.includes('[aria-pressed="true"]'))!;
    expect(button.matches(selectorFor(pressed))).toBe(true);
  });

  it("adds Card coloured hover paint only when interactive and keeps the real hover media gate", () => {
    depth();
    const { container, rerender } = render(<Card>Body</Card>);
    const card = container.firstElementChild!;
    const hover = styleRules("depth-dark", "background-color", "var(--action-tint)")[0];
    expect(matchesPaint(card)).toBe(true);
    expect(card.matches(stateSelector(hover, { hover: true }))).toBe(false);
    rerender(<Card interactive>Body</Card>);
    expect(card.matches(stateSelector(hover, { hover: true }))).toBe(true);
    expect(mediaConditions(hover)).toContain("(hover: hover)");
  });

  it("resolves actual Input and Select disabled fill independently of surface roles", () => {
    depth();
    const { rerender } = render(<><Input label="Name" /><Select label="Language"><option>English</option></Select></>);
    const fields = [screen.getByLabelText("Name"), screen.getByLabelText("Language")];
    const base = styleRules("depth-dark", "--depth-fill-base", "var(--control-bg)")[0];
    const disabled = styleRules("depth-dark", "--depth-fill-base", "var(--control-disabled-bg)")
      .find((rule) => rule.selector.includes(":disabled"))!;
    for (const field of fields) {
      expect(field.matches(selectorFor(base))).toBe(true);
      expect(field.matches(selectorFor(disabled))).toBe(false);
      expect(matchesPaint(field)).toBe(false);
    }
    rerender(<><Input label="Name" disabled /><Select label="Language" disabled><option>English</option></Select></>);
    for (const field of fields) expect(field.matches(selectorFor(disabled))).toBe(true);
  });

  it("paints the Modal panel while keeping the real tooltip outside surface treatments", () => {
    depth();
    render(<Modal open onClose={vi.fn()} title="Review"><Tooltip content="Explanation"><button>Help</button></Tooltip></Modal>);
    const panel = screen.getByText("Review").closest('[data-ui-surface="float"]')!;
    expect(matchesPaint(panel)).toBe(true);
    fireEvent.focus(screen.getByRole("button", { name: "Help" }));
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveAttribute("data-ui-tooltip");
    expect(matchesPaint(tooltip)).toBe(false);
    document.documentElement.dataset.style = "classic";
    expect(styleRules("classic", "--style-chrome-shadow").some((rule) => tooltip.matches(selectorFor(rule)))).toBe(false);
  });

  it("keeps settings borders and fill explicit through disclosure transitions and landing cues", () => {
    depth();
    render(<><SettingsAdvanced><p>Details</p></SettingsAdvanced><SettingsCard title="Preferences"><p>Values</p></SettingsCard></>);
    const advanced = screen.getByRole("button", { name: "Advanced" });
    const card = screen.getByText("Preferences").closest('[data-ui-surface="card"]')!;
    const hover = styleRules("depth-dark", "--depth-fill-base", "var(--surface-inset)")
      .find((rule) => rule.selector.includes('[data-ui-hover="inset"]'))!;
    expect(advanced.matches(stateSelector(hover, { hover: true }))).toBe(true);
    fireEvent.click(advanced);
    expect(screen.getByText("Details")).toBeInTheDocument();
    expect(advanced).toHaveAttribute("data-ui-border", "full");
    card.classList.add("settings-target");
    expect(styleRules("depth-gradient", "animation-fill-mode", "none")
      .some((rule) => card.matches(selectorFor(rule)))).toBe(true);
    document.documentElement.dataset.style = "classic";
    expect(styleRules("classic", "--style-chrome-shadow", "var(--classic-reading-raised)")
      .some((rule) => card.matches(selectorFor(rule)))).toBe(false);
  });

  it("connects semantic SettingsCallout fill to local mixing while excluding raised gradients", () => {
    depth();
    render(<SettingsCallout tone="info" message="Connected" />);
    const callout = screen.getByText("Connected").closest('[data-ui-surface="pane"]')!;
    const fill = styleRules("depth-dark", "--depth-fill-base", "var(--status-info-bg)")[0];
    expect(callout.matches(selectorFor(fill))).toBe(true);
    expect(matchesPaint(callout)).toBe(false);
  });
});
