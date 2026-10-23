import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { loadingButtonProps } from "./loadingButton";

/**
 * Raw buttons that run their own request spread these props, as the Button
 * primitive does, so they keep keyboard focus while they wait (#539, #614).
 * Chrome moves focus from a disabled button to the page body; jsdom does not,
 * so these check the button is never disabled while busy.
 */
describe("loadingButtonProps on a raw button", () => {
  it("keeps a busy button focusable and marks it disabled and busy", () => {
    render(
      <button type="button" {...loadingButtonProps(true, true, vi.fn())}>
        Check for updates
      </button>,
    );

    const button = screen.getByRole("button", { name: "Check for updates" });
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveAttribute("aria-busy", "true");
    button.focus();
    expect(button).toHaveFocus();
  });

  it("ignores a click while busy", () => {
    const onClick = vi.fn();
    render(
      <button type="button" {...loadingButtonProps(true, false, onClick)}>
        Sync now
      </button>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));

    expect(onClick).not.toHaveBeenCalled();
  });

  // Enter in a field sends a click to the form's submit button, so this
  // covers the sign-in forms too.
  it.each([
    { busy: false, sent: 1 },
    { busy: true, sent: 0 },
  ])("sends its form $sent time(s) when busy is $busy", ({ busy, sent }) => {
    const onSubmit = vi.fn((event: { preventDefault: () => void }) => event.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <button type="submit" {...loadingButtonProps(busy)}>
          Sign in
        </button>
      </form>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(onSubmit).toHaveBeenCalledTimes(sent);
  });

  it("leaves an idle button to its own disabled state and click", () => {
    const onClick = vi.fn();
    const { rerender } = render(
      <button type="button" {...loadingButtonProps(false, true, onClick)}>
        Save
      </button>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toBeDisabled();
    expect(button).not.toHaveAttribute("aria-busy");

    rerender(
      <button type="button" {...loadingButtonProps(false, false, onClick)}>
        Save
      </button>,
    );
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
