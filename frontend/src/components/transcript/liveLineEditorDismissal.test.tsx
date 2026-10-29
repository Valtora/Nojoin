import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LINE_CHANGED_MESSAGE, transcriptLineActions } from "@/components/recordings/_hooks/transcriptLineActions";
import type { TranscriptSegment } from "@/types";
import type { LiveLineHandlers } from "./LiveLineEditor";
import LiveLineMenu from "./LiveLineMenu";

const text = vi.fn();
vi.mock("@/lib/api", () => ({
  getGlobalSpeakers: vi.fn().mockResolvedValue([]),
  updateTranscriptUtteranceText: (...args: unknown[]) => text(...args),
  updateTranscriptUtteranceSpeaker: vi.fn(),
}));

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
});

afterEach(() => {
  vi.restoreAllMocks();
  text.mockReset();
});

function renderEditor(handlers: Partial<LiveLineHandlers> = {}) {
  const all: LiveLineHandlers = {
    editText: vi.fn().mockResolvedValue(true), changeSpeaker: vi.fn().mockResolvedValue(true),
    split: vi.fn().mockResolvedValue(true), ...handlers,
  };
  render(<LiveLineMenu segment={segment} handlers={all} onNoteOnLine={vi.fn()} />);
  const trigger = screen.getByRole("button", { name: "Actions for the line at 00:00" });
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
  return { handlers: all, trigger, box: screen.getByRole("textbox", { name: "Line text" }) };
}

describe("live line editor dismissal", () => {
  it("closes on a press outside only while the text is as it opened", () => {
    const { box } = renderEditor();
    fireEvent.change(box, { target: { value: "Alpha better" } });
    fireEvent.pointerDown(document.body);
    expect(screen.getByRole("dialog", { name: "Edit line text" })).toBeInTheDocument();

    fireEvent.change(box, { target: { value: "Alpha beta" } });
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps Tab inside once the text changes, and leaves by the ⋯ button while it is as it opened", () => {
    const { box, trigger } = renderEditor();
    const cancel = screen.getByRole("button", { name: "Cancel" });
    fireEvent.change(box, { target: { value: "Alpha better" } });
    cancel.focus();
    fireEvent.keyDown(cancel, { key: "Tab" });
    expect(box).toHaveFocus();
    fireEvent.keyDown(box, { key: "Tab", shiftKey: true });
    expect(cancel).toHaveFocus();

    fireEvent.change(box, { target: { value: "Alpha beta" } });
    fireEvent.keyDown(cancel, { key: "Tab" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("puts focus back in the text after a refusal, keeping what was typed", async () => {
    const { box } = renderEditor({ editText: vi.fn().mockResolvedValue(false) });
    fireEvent.change(box, { target: { value: "My correction" } });
    // Pressed as a pointer or Enter would, from the button itself.
    const save = screen.getByRole("button", { name: "Save" });
    save.focus();
    fireEvent.click(save);
    await waitFor(() => expect(save).toBeEnabled());
    expect(box).toHaveFocus();
    expect(box).toHaveValue("My correction");
  });

  it("closes with a plain notice when the line changed since it opened", async () => {
    text.mockRejectedValueOnce({ response: { status: 409, data: { detail: "Utterance revision conflict" } } });
    const notify = vi.fn();
    const actions = transcriptLineActions(notify);
    const { box, trigger } = renderEditor({
      editText: (line, value) => actions.editTranscriptLine("r1", line, value),
    });
    fireEvent.change(box, { target: { value: "My correction" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(text).toHaveBeenCalledWith("r1", "u1", "My correction", 3);
    expect(notify).toHaveBeenCalledWith(LINE_CHANGED_MESSAGE);
    expect(trigger).toHaveFocus();
  });
});
