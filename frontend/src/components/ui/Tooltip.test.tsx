import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Modal from "./Modal";
import Tooltip, { InfoTip } from "./Tooltip";

const TEXT = "Applied when you stop.";

const renderInfo = () => {
  render(<InfoTip label="About Max Speakers" content={TEXT} />);
  return screen.getByRole("button", { name: "About Max Speakers" });
};

/** A tap: a press, the focus it may give the button, then the click. */
const tap = (target: HTMLElement) => {
  fireEvent.pointerDown(target, { pointerType: "touch" });
  fireEvent.focus(target);
  fireEvent.click(target);
};

const bubble = () => screen.getByText(TEXT);

describe("InfoTip", () => {
  it("is a named button described by its text, which stays hidden until asked for", () => {
    const button = renderInfo();

    expect(button).toHaveAccessibleDescription(TEXT);
    expect(bubble()).toHaveAttribute("role", "tooltip");
    expect(bubble()).not.toBeVisible();
  });

  it("opens on a mouse hover and stays while the pointer crosses to it", async () => {
    const button = renderInfo();

    fireEvent.pointerEnter(button, { pointerType: "mouse" });
    expect(bubble()).toBeVisible();

    // Leaving and coming back over the bubble keeps it open.
    fireEvent.pointerLeave(button, { pointerType: "mouse" });
    fireEvent.pointerEnter(bubble(), { pointerType: "mouse" });
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(bubble()).toBeVisible();

    fireEvent.pointerLeave(bubble(), { pointerType: "mouse" });
    await waitFor(() => expect(bubble()).not.toBeVisible());
  });

  it("opens on keyboard focus and closes when focus leaves", () => {
    const button = renderInfo();

    fireEvent.focus(button);
    expect(bubble()).toBeVisible();

    fireEvent.blur(button);
    expect(bubble()).not.toBeVisible();
  });

  // A phone has no hover, and the focus a tap gives is not a keyboard's.
  it("opens on a tap and closes on a second tap or a tap elsewhere", () => {
    const button = renderInfo();

    fireEvent.pointerEnter(button, { pointerType: "touch" });
    expect(bubble()).not.toBeVisible();

    tap(button);
    expect(bubble()).toBeVisible();
    tap(button);
    expect(bubble()).not.toBeVisible();

    tap(button);
    expect(bubble()).toBeVisible();
    fireEvent.pointerDown(document.body, { pointerType: "touch" });
    expect(bubble()).not.toBeVisible();
  });

  it("closes on Escape without closing the dialog it sits in", () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Import audio">
        <InfoTip label="About Max Speakers" content={TEXT} />
      </Modal>,
    );
    const button = screen.getByRole("button", { name: "About Max Speakers" });

    fireEvent.focus(button);
    expect(screen.getByRole("tooltip")).toBeVisible();
    fireEvent.keyDown(button, { key: "Escape" });

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();

    // With nothing left to close, Escape is the dialog's again.
    fireEvent.keyDown(button, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("stays closed after Escape until the pointer comes back", () => {
    const button = renderInfo();

    fireEvent.pointerEnter(button, { pointerType: "mouse" });
    fireEvent.keyDown(button, { key: "Escape" });
    expect(bubble()).not.toBeVisible();

    fireEvent.pointerLeave(button, { pointerType: "mouse" });
    fireEvent.pointerEnter(button, { pointerType: "mouse" });
    expect(bubble()).toBeVisible();
  });
});

describe("Tooltip", () => {
  it("shows on hover and focus of what it wraps, and Escape closes it", () => {
    render(
      <Tooltip content={TEXT}>
        <button type="button">Help</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "Help" });

    fireEvent.focus(trigger);
    expect(bubble()).toBeVisible();
    fireEvent.keyDown(trigger, { key: "Escape" });
    expect(bubble()).not.toBeVisible();
    fireEvent.blur(trigger);

    // A press shows nothing: only an info button pins its bubble.
    fireEvent.pointerDown(trigger, { pointerType: "mouse" });
    fireEvent.focus(trigger);
    fireEvent.click(trigger);
    expect(bubble()).not.toBeVisible();
  });
});
