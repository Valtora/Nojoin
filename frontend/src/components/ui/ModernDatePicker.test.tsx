import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Modal from "./Modal";
import ModernDatePicker from "./ModernDatePicker";

/**
 * The picker's usual home is inside a modal, and that is where its positioning
 * used to fail: a modal panel hides its own overflow and scrolls its body, so
 * an absolutely positioned popper is a child of that scroll box. The library
 * measured the free space inside the box rather than in the window, flipped the
 * calendar upwards for want of room, and the panel clipped everything that
 * crossed its header (issue #242). Asking for the fixed strategy is what takes
 * the popper out of the box, so that is what is asserted here. jsdom does no
 * layout, so the rest of the fix -- the viewport bounds and the stacked mobile
 * layout -- lives in globals.css and is verified in a browser.
 */
describe("ModernDatePicker", () => {
  it("positions the calendar against the viewport rather than its scroll box", () => {
    render(<ModernDatePicker selected={null} onChange={vi.fn()} placeholderText="Pick one" />);

    fireEvent.click(screen.getByRole("button", { name: "Pick one" }));

    const popper = document.querySelector(".react-datepicker-popper");
    expect(popper).not.toBeNull();
    expect((popper as HTMLElement).style.position).toBe("fixed");
  });

  it("keeps the calendar inside the modal, where the focus trap can reach it", () => {
    render(
      <Modal open onClose={vi.fn()} title="Import Audio">
        <ModernDatePicker selected={null} onChange={vi.fn()} placeholderText="Pick one" />
      </Modal>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Pick one" }));

    // Portalling the popper to the body would escape the clipping too, and take
    // it outside the dialog: Headless UI would read a click on a day as a click
    // outside the panel and close the modal under the user.
    const popper = document.querySelector(".react-datepicker-popper");
    expect(popper).not.toBeNull();
    const dialogs = screen.getAllByRole("dialog");
    expect(dialogs.some((dialog) => dialog.contains(popper))).toBe(true);
  });
});
