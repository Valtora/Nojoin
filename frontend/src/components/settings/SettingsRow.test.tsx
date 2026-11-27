import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import SettingsRow from "./SettingsRow";
import { SETTINGS_CONTROL_COLUMN_WIDE } from "./settingsControls";

describe("SettingsRow", () => {
  it("renders label, description, and the control", () => {
    render(
      <SettingsRow label="Microphone" description="Pick the input device.">
        <button type="button">Choose</button>
      </SettingsRow>,
    );

    expect(screen.getByText("Microphone")).toBeTruthy();
    expect(screen.getByText("Pick the input device.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Choose" })).toBeTruthy();
  });

  it("exposes the id as a scroll anchor on the root", () => {
    const { container } = render(
      <SettingsRow id="capture-microphone" label="Microphone" />,
    );

    const root = container.firstElementChild as HTMLElement;
    expect(root.id).toBe("capture-microphone");
    expect(root.className).toContain("scroll-mt-24");
  });

  // jsdom cannot evaluate container queries, so the contract under test is the
  // class strings: the row is its own container and every beside-layout class
  // keys off the row's width rather than a viewport breakpoint.
  it("marks the root as a container", () => {
    const { container } = render(<SettingsRow label="Microphone" />);

    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain("@container");
  });

  it("switches to the beside-layout with container variants, not viewport ones", () => {
    const { container } = render(
      <SettingsRow label="Microphone" description="Pick the input device.">
        <button type="button">Choose</button>
      </SettingsRow>,
    );

    const root = container.firstElementChild as HTMLElement;
    const layout = root.firstElementChild as HTMLElement;
    expect(layout.className).toContain("flex-col");
    expect(layout.className).toContain("@min-[26rem]:flex-row");
    expect(layout.className).toContain("@min-[26rem]:justify-between");

    const label = layout.children[0] as HTMLElement;
    expect(label.className).toContain("@min-[26rem]:max-w-md");

    const control = layout.children[1] as HTMLElement;
    expect(control.className).toContain("w-full");
    expect(control.className).toContain("@min-[26rem]:min-w-56");

    // The stale viewport variants must not creep back in.
    expect(root.outerHTML).not.toMatch(/sm:flex-row|sm:min-w-56|sm:max-w-md/);
  });

  // The row turns side by side at 26rem, the control's 14rem floor plus the
  // 1.5rem gap plus 10.5rem for the label. The control column's max width
  // keeps that 10.5rem at every width beside it, for a content-sized column
  // and for one a row fixes (#1043). Read from the row's own classes.
  it.each([
    ["content-sized", undefined],
    ["fixed wide", SETTINGS_CONTROL_COLUMN_WIDE],
  ])("never lets a %s control column take the label's 10.5rem", (_, controlClassName) => {
    const { container } = render(
      <SettingsRow label="Colour palette" description="Pick one." controlClassName={controlClassName}>
        <select aria-label="Colour palette" />
      </SettingsRow>,
    );

    const layout = (container.firstElementChild as HTMLElement).firstElementChild as HTMLElement;
    const control = (layout.children[1] as HTMLElement).className;
    const rem = (pattern: RegExp, text: string) => Number(text.match(pattern)?.[1]);
    const beside = rem(/@min-\[(\d+(?:\.\d+)?)rem\]:flex-row/, layout.className);
    const gap = rem(/@min-\[[\d.]+rem\]:gap-(\d+)/, layout.className) / 4;
    const floor = rem(/@min-\[[\d.]+rem\]:min-w-(\d+)/, control) / 4;
    const cap = control.match(
      /@min-\[(\d+(?:\.\d+)?)rem\]:max-w-\[min\((\d+(?:\.\d+)?)rem,calc\(100%_-_(\d+(?:\.\d+)?)rem\)\)\]/,
    );
    expect(cap).not.toBeNull();
    const [, from, widest, reserve] = (cap as RegExpMatchArray).map(Number);

    expect(from).toBe(beside);
    // Its widest is max-w-sm, as before.
    expect(widest).toBe(24);
    // The room it leaves holds the gap and a 10.5rem label...
    expect(reserve - gap).toBeGreaterThanOrEqual(10.5);
    // ...and where the row turns side by side, its floor does not take it back.
    expect(beside - reserve).toBeGreaterThanOrEqual(floor);
  });

  it("lets controlClassName override the control's container variants", () => {
    const { container } = render(
      <SettingsRow
        label="Connect"
        controlClassName="@min-[26rem]:min-w-0 @min-[26rem]:flex @min-[26rem]:justify-end"
      >
        <button type="button">Connect</button>
      </SettingsRow>,
    );

    const layout = (container.firstElementChild as HTMLElement)
      .firstElementChild as HTMLElement;
    const control = layout.children[1] as HTMLElement;
    expect(control.className).toContain("@min-[26rem]:min-w-0");
    expect(control.className).not.toContain("@min-[26rem]:min-w-56");
  });
});
