import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TranscriptSegment } from "@/types";
import type { LiveLineHandlers } from "./LiveLineEditor";
import SharedLiveTranscript from "@/test/SharedLiveTranscript";

const segment: TranscriptSegment = {
  id: "u1", revision: 3, start: 0, end: 2, speaker: "UNKNOWN", text: "Alpha beta",
  provisional: true, settling_required: true, locked: true, text_locked: true,
  words: [
    { id: "w1", order: 0, word: "Alpha", start_ms: 0, end_ms: 500, locked: true },
    { id: "w2", order: 1, word: "beta", start_ms: 700, end_ms: 1600, locked: true },
  ],
};

beforeEach(() => {
  const nativeFocus = HTMLElement.prototype.focus;
  // JSDOM allows focus inside visibility:hidden panels; browsers reject it.
  vi.spyOn(HTMLElement.prototype, "focus").mockImplementation(function (this: HTMLElement, options?: FocusOptions) {
    if (getComputedStyle(this).visibility === "hidden") return;
    nativeFocus.call(this, options);
  });
  const hidden = document.createElement("button");
  hidden.style.visibility = "hidden";
  document.body.append(hidden);
  const before = document.activeElement;
  expect(getComputedStyle(hidden).visibility).toBe("hidden");
  hidden.focus();
  expect(document.activeElement).toBe(before);
  hidden.remove();
});

afterEach(() => vi.restoreAllMocks());

function renderMenu() {
  const handlers: LiveLineHandlers = {
    editText: vi.fn().mockResolvedValue(true), changeSpeaker: vi.fn().mockResolvedValue(true),
    split: vi.fn().mockResolvedValue(true),
  };
  render(<SharedLiveTranscript segments={[segment]} hasLoaded lineHandlers={handlers} onNoteOnLine={vi.fn()} />);
  const trigger = screen.getByRole("button", { name: "Actions for the line at 00:00" });
  const openSplit = () => {
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("menuitem", { name: "Split line" }));
    return screen.getByRole("combobox", { name: "Split before word" });
  };
  return { handlers, trigger, openSplit };
}

describe("split picker focus", () => {
  it("focuses the visible picker on every open and returns to its trigger on Escape", () => {
    const { handlers, trigger, openSplit } = renderMenu();
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const picker = openSplit();
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
      expect(picker).toHaveFocus();
      fireEvent.keyDown(document.activeElement!, { key: "Escape" });
      expect(screen.queryByRole("dialog", { name: "Split line" })).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    }
    expect(handlers.split).not.toHaveBeenCalled();
  });

  it("keeps focus on Cancel when the anchored picker is repositioned", () => {
    const { openSplit } = renderMenu();
    expect(openSplit()).toHaveFocus();
    const cancel = screen.getByRole("button", { name: "Cancel" });
    cancel.focus();
    fireEvent.resize(window);
    fireEvent.scroll(window);
    expect(cancel).toHaveFocus();
  });
});
