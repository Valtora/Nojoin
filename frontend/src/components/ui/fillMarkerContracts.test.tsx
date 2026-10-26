import { readFileSync } from "node:fs";
import postcss from "postcss";
import { describe, expect, it, vi } from "vitest";

import { fillAliases, fillMarkerViolations, renderWithFillContract as render } from "@/test/fillMarkerContracts";
import SettingsBlock from "../settings/SettingsBlock";
import SettingsCallout from "../settings/SettingsCallout";
import Button from "./Button";
import IconButton from "./IconButton";
import Input from "./Input";
import Select from "./Select";
import { Switch } from "./Switch";

describe("fill marker agreement", () => {
  it.each([
    ['data-ui-fill="card" class="bg-surface-inset"', "disagrees"],
    ['data-ui-fill="card"', "missing bg-surface-card"],
    ['class="bg-surface-card"', "missing data-ui-fill"],
    ['data-ui-fill="card" class="hover:bg-surface-card"', "missing bg-surface-card"],
    ['data-ui-fill="inset-translucent" class="bg-surface-inset"', "disagrees"],
    ['data-ui-fill="typo" class="bg-surface-card"', "unknown"],
  ])("rejects the broken recipe %s", (attributes, message) => {
    const fixture = document.createElement("div");
    fixture.innerHTML = `<div data-ui="surface" ${attributes}></div>`
      + '<div data-ui="surface" data-ui-fill="card" class="bg-surface-card"></div>';
    expect(fillMarkerViolations(fixture)).toHaveLength(1);
    expect(fillMarkerViolations(fixture)[0]).toContain(message);
  });

  it("does not mistake decoration, transparent controls or other state fills for local fills", () => {
    const fixture = document.createElement("div");
    fixture.innerHTML = '<div class="bg-surface-card"></div>'
      + '<button data-ui="button" class="bg-transparent hover:bg-surface-inset"></button>'
      + '<span data-ui="badge" class="bg-status-info-bg"></span>'
      + '<input data-ui="input" type="range" class="bg-surface-inset">'
      + '<div data-ui-reading="bubble" class="bg-status-success-bg"></div>'
      + '<div data-ui="surface" data-ui-tooltip class="bg-tooltip-bg"></div>'
      + '<button data-ui="input" data-ui-fill="control" class="bg-control-bg disabled:bg-control-disabled-bg"></button>';
    expect(fillMarkerViolations(fixture)).toEqual([]);
  });

  it("requires a fill marker on reading panes independently of speaker-colour bubbles", () => {
    const fixture = document.createElement("aside");
    fixture.setAttribute("data-ui-reading", "pane");
    fixture.className = "bg-surface-inset";
    expect(fillMarkerViolations(fixture)).toEqual(["aside: missing data-ui-fill for bg-surface-inset"]);
  });

  it.each([false, true])("rejects swapped dynamic recipe branches with selected=%s", (selected) => {
    expect(() => render(
      <button
        data-ui="button"
        data-ui-fill={selected ? "card" : "action"}
        className={selected ? "bg-action" : "bg-surface-card"}
      >Broken variant</button>,
    )).toThrow("disagrees");
  });

  it.each(["primary", "secondary", "ghost", "danger"] as const)(
    "checks the rendered %s button and independent icon-button variants", (variant) => {
      render(<><Button variant={variant}>Save</Button><IconButton variant={variant} aria-label="Save" icon={<span />} /></>);
    },
  );

  it.each(["neutral", "info", "success", "warning", "error"] as const)(
    "checks the rendered %s callout recipe", (tone) => {
      render(<SettingsCallout tone={tone} message="Details" />);
    },
  );

  it.each([false, true])("checks shared fields, switches and settings blocks with state=%s", (state) => {
    render(<>
      <Input disabled={state} label="Name" />
      <Select disabled={state} label="Language"><option>English</option></Select>
      <Switch checked={state} onCheckedChange={vi.fn()} />
      <SettingsBlock inset={state} label="Details">Body</SettingsBlock>
    </>);
  });

  it.each(Object.entries(fillAliases))("checks the CSS-owned %s alias against its real declaration", (alias, fill) => {
    const declarations: string[] = [];
    const sourcePath = "../../app/globals.css";
    postcss.parse(readFileSync(new URL(sourcePath, import.meta.url), "utf8"))
      .walkRules(`.${alias}`, (rule) => {
        rule.walkDecls("background-color", (declaration) => { declarations.push(declaration.value); });
      });
    expect(declarations).toEqual([`var(--surface-${fill})`]);
  });
});
