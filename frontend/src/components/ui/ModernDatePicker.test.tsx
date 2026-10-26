import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Modal from "./Modal";
import ModernDatePicker from "./ModernDatePicker";
import TaskDeadlineModal from "./TaskDeadlineModal";
import { paintSelectors, selectorFor } from "@/test/depthSelectors";
import { styleRules, styleSources } from "@/test/styleRules";

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

  // react-datepicker passes each day at midnight, so comparing it with a value
  // that carries a time never matched, and the selected day kept body text on
  // the action fill (3.46:1 in light, 1.60:1 in Marigold dark) (#617).
  it("labels the selected day for its fill when the value has a time", () => {
    render(
      <ModernDatePicker
        selected={new Date(2026, 9, 14, 10, 30)}
        onChange={vi.fn()}
        showTimeSelect
        open
      />,
    );

    const day = document.querySelector(".react-datepicker__day--selected");
    expect(day?.textContent).toBe("14");
    expect(day?.className).toContain("!text-action-on");
    expect(day?.className).not.toContain("!text-foreground");
  });

  it("connects the vendor's actual selected day and time to the dark filled perimeter", () => {
    render(<ModernDatePicker selected={new Date(2026, 9, 14, 10, 30)} onChange={vi.fn()} showTimeSelect open />);
    const oldStyle = document.documentElement.getAttribute("data-style");
    const oldDark = document.documentElement.classList.contains("dark");
    document.documentElement.dataset.style = "depth";
    document.documentElement.classList.add("dark");
    try {
      const perimeter = styleRules("depth-dark", "box-shadow")[0];
      for (const selected of [".react-datepicker__day--selected", ".react-datepicker__time-list-item--selected"]) {
        const node = document.querySelector(selected);
        expect(node).not.toBeNull();
        expect(node!.matches(selectorFor(perimeter))).toBe(true);
      }
    } finally {
      if (oldStyle === null) document.documentElement.removeAttribute("data-style");
      else document.documentElement.setAttribute("data-style", oldStyle);
      document.documentElement.classList.toggle("dark", oldDark);
    }
  });

  it("keeps the real inline deadline calendar out of raised gradients", () => {
    render(<TaskDeadlineModal isOpen taskTitle="Review" value={new Date(2026, 9, 14, 10, 30)}
      onClose={vi.fn()} onSave={vi.fn()} />);
    const calendar = document.querySelector(".react-datepicker.task-deadline-calendar");
    expect(calendar).not.toBeNull();
    const oldStyle = document.documentElement.getAttribute("data-style");
    const oldDark = document.documentElement.classList.contains("dark");
    document.documentElement.dataset.style = "depth";
    try {
      for (const dark of [false, true]) {
        document.documentElement.classList.toggle("dark", dark);
        expect(paintSelectors(styleSources["depth-gradient"]).some((selector) => calendar!.matches(selector))).toBe(false);
      }
    } finally {
      if (oldStyle === null) document.documentElement.removeAttribute("data-style");
      else document.documentElement.setAttribute("data-style", oldStyle);
      document.documentElement.classList.toggle("dark", oldDark);
    }
  });

  it("keeps body text on the days around it", () => {
    render(
      <ModernDatePicker
        selected={new Date(2026, 9, 14, 10, 30)}
        onChange={vi.fn()}
        showTimeSelect
        open
      />,
    );

    const others = [...document.querySelectorAll(".react-datepicker__day")].filter(
      (day) => !day.classList.contains("react-datepicker__day--selected"),
    );
    expect(others.length).toBeGreaterThan(27);
    expect(others.every((day) => day.className.includes("!text-foreground"))).toBe(true);
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
