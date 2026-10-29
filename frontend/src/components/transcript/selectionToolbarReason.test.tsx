import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/components/LiveTranscriptPanel";
import type { TranscriptSegment } from "@/types";
import { SELECTION_SETTLE_MS, type CapturedSelection } from "./_hooks/useCapturedSelection";
import { TranscriptSelectionToolbar } from "./TranscriptSelectionToolbar";

const REASON = "Selected words are still settling.";
const segment = (): TranscriptSegment => ({
  id: "line", revision: 1, start: 1, end: 2, text: "Pending", speaker: "UNKNOWN",
  settling_required: true, provisional: true, locked: false,
  words: [{ id: "word", order: 0, word: "Pending", start_ms: 1000, end_ms: 2000, locked: false }],
});

afterEach(() => {
  document.getSelection()?.removeAllRanges();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function selectWord() {
  const range = document.createRange();
  range.selectNodeContents(document.querySelector('[data-word-id="word"]')!);
  document.getSelection()!.addRange(range);
  act(() => {
    document.dispatchEvent(new Event("selectionchange"));
    vi.advanceTimersByTime(SELECTION_SETTLE_MS);
  });
}

function captured(): CapturedSelection {
  return {
    seq: 1, text: "Pending", segmentId: "line", range: document.createRange(),
    box: { top: 700, bottom: 720, left: 100, right: 200 },
    selection: { startMs: 1000, endMs: 2000, quotedText: "Pending" },
  };
}

it("explains the disabled note visibly and accessibly while Copy keeps the selected text", async () => {
  vi.useFakeTimers();
  const onNote = vi.fn();
  const writeText = vi.fn().mockResolvedValue(undefined);
  const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  try {
    render(<LiveTranscriptPanel segments={[segment()]} hasLoaded onNoteOnLine={onNote} />);
    selectWord();
    const toolbar = screen.getByRole("toolbar");
    const add = within(toolbar).getByRole("button", { name: "Add note" });
    const reason = within(toolbar).getByText(REASON);
    expect(reason).toBeVisible();
    expect(add).toBeDisabled();
    expect(add).toHaveAccessibleDescription(REASON);
    expect(document.getElementById(add.getAttribute("aria-describedby")!)).toBe(reason);
    expect(document.getSelection()!.toString()).toBe("Pending");
    fireEvent.click(add);
    expect(onNote).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(within(toolbar).getByRole("button", { name: "Copy" }));
    });
    expect(writeText).toHaveBeenCalledWith("Pending");
    expect(screen.queryByRole("toolbar")).not.toBeInTheDocument();
  } finally {
    if (originalClipboard) Object.defineProperty(navigator, "clipboard", originalClipboard);
    else Reflect.deleteProperty(navigator, "clipboard");
  }
});

it("removes the explanation and description reference when the selected words lock", () => {
  vi.useFakeTimers();
  const onNote = vi.fn();
  const pending = segment();
  const view = render(<LiveTranscriptPanel segments={[pending]} hasLoaded onNoteOnLine={onNote} />);
  selectWord();
  const add = screen.getByRole("button", { name: "Add note" });
  expect(add).toHaveAccessibleDescription(REASON);
  view.rerender(<LiveTranscriptPanel hasLoaded onNoteOnLine={onNote} segments={[{
    ...pending, revision: 2, words: pending.words!.map((word) => ({ ...word, locked: true })),
  }]} />);
  expect(add).toBeEnabled();
  expect(add).not.toHaveAttribute("aria-describedby");
  expect(screen.queryByText(REASON)).not.toBeInTheDocument();
  fireEvent.click(add);
  expect(onNote).toHaveBeenCalledWith(expect.objectContaining({ quotedText: "Pending" }));
});

it("gives simultaneous toolbar descriptions separate IDs", () => {
  render(<>
    <TranscriptSelectionToolbar captured={captured()} onClose={vi.fn()} disabledReason={REASON} />
    <TranscriptSelectionToolbar captured={captured()} onClose={vi.fn()} disabledReason={REASON} />
  </>);
  const buttons = screen.getAllByRole("button", { name: "Add note" });
  const ids = buttons.map((button) => button.getAttribute("aria-describedby"));
  expect(ids[0]).toBeTruthy();
  expect(ids[0]).not.toBe(ids[1]);
  for (const button of buttons) {
    const reason = document.getElementById(button.getAttribute("aria-describedby")!);
    expect(reason).toBeVisible();
    expect(button.closest('[role="toolbar"]')).toContainElement(reason);
    expect(button).toHaveAccessibleDescription(REASON);
  }
});

it("repositions above the selection when the reason changes the panel height", () => {
  const height = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get");
  height.mockImplementation(function (this: HTMLElement) {
    return this.textContent?.includes(REASON) ? 80 : 40;
  });
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(220);
  const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect");
  rect.mockReturnValue({ top: 700, bottom: 720, left: 100, right: 200, width: 100, height: 20,
    x: 100, y: 700, toJSON: () => ({}) });
  const selection = captured();
  const onClose = vi.fn();
  const view = render(<TranscriptSelectionToolbar captured={selection} onClose={onClose} />);
  const toolbar = screen.getByRole("toolbar");
  expect(toolbar).toHaveStyle({ top: "652px" });
  view.rerender(<TranscriptSelectionToolbar captured={selection} onClose={onClose} disabledReason={REASON} />);
  expect(toolbar).toHaveStyle({ top: "612px" });
  view.rerender(<TranscriptSelectionToolbar captured={selection} onClose={onClose} />);
  expect(toolbar).toHaveStyle({ top: "652px" });
});
