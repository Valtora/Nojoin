import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import SettingsRow from "./SettingsRow";

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
