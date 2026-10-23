import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/components/LiveTranscriptPanel";
import TranscriptView from "@/components/TranscriptView";
import type { TranscriptSegment } from "@/types";

import { SELECTION_SETTLE_MS } from "./_hooks/useCapturedSelection";

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

const originalScrollIntoView = Element.prototype.scrollIntoView;
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterAll(() => {
  Element.prototype.scrollIntoView = originalScrollIntoView;
});
beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  document.getSelection()?.removeAllRanges();
  vi.useRealTimers();
});

// "Forty thousand for the venue": 28 characters over 12.0 s to 15.5 s, so
// 125 ms a character.
const SEGMENTS: TranscriptSegment[] = [
  { id: "u1", speaker: "SPEAKER_0", start: 12, end: 15.5, text: "Forty thousand for the venue" },
  { id: "u2", speaker: "SPEAKER_1", start: 16, end: 18, text: "Catering is extra" },
];

function renderFinished(overrides: Partial<ComponentProps<typeof TranscriptView>> = {}) {
  const props: ComponentProps<typeof TranscriptView> = {
    recordingId: "rec-1",
    segments: SEGMENTS,
    currentTime: -1,
    onPlaySegment: vi.fn(),
    isPlaying: false,
    onPause: vi.fn(),
    onResume: vi.fn(),
    speakerMap: { SPEAKER_0: "Alex", SPEAKER_1: "Blair" },
    speakers: [],
    globalSpeakers: [],
    onRenameSpeaker: vi.fn(),
    onUpdateSegmentSpeaker: vi.fn(),
    onUpdateSegmentText: vi.fn(),
    onFindAndReplace: vi.fn(),
    speakerColors: { SPEAKER_0: "orange", SPEAKER_1: "blue" },
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    canUndo: false,
    canRedo: false,
    onExport: vi.fn(),
    onAddNote: vi.fn(() => null),
    ...overrides,
  };
  render(<TranscriptView {...props} />);
  return props;
}

/** The text of the `index`th line, as the page draws it. */
const lineText = (index: number) => document.querySelectorAll("p[data-segment-id]")[index] as HTMLElement;

/**
 * Selects from `from` characters into one line to `to` into another, as a
 * drag with the mouse does, and lets it settle.
 */
function drag(from: [number, number], to: [number, number]) {
  const textAt = (index: number) => lineText(index).firstChild as Text;
  fireEvent.pointerDown(lineText(from[0]));
  const range = document.createRange();
  range.setStart(textAt(from[0]), from[1]);
  range.setEnd(textAt(to[0]), to[1]);
  const selection = document.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  act(() => {
    document.dispatchEvent(new Event("selectionchange"));
  });
  fireEvent.pointerUp(lineText(to[0]));
  act(() => {
    vi.advanceTimersByTime(SELECTION_SETTLE_MS);
  });
}

const toolbar = () => screen.getByRole("toolbar", { name: "Selected text" });
const noToolbar = () => expect(screen.queryByRole("toolbar", { name: "Selected text" })).not.toBeInTheDocument();

describe("selected text in the finished transcript", () => {
  it("offers a note on the words selected in one line, sent over their span", () => {
    const props = renderFinished();
    drag([0, 6], [0, 18]);

    fireEvent.click(within(toolbar()).getByRole("button", { name: "Add note" }));
    act(() => {
      vi.advanceTimersByTime(50);
    });
    const popover = screen.getByRole("dialog", { name: "Note on the selected text at 00:12" });
    expect(popover).toHaveTextContent("thousand for");
    const box = within(popover).getByRole("textbox", { name: "Note" });
    fireEvent.change(box, { target: { value: "Is that with tax?" } });
    fireEvent.keyDown(box, { key: "Enter" });

    expect(props.onAddNote).toHaveBeenCalledWith({
      kind: "selection",
      body: "Is that with tax?",
      anchorMs: null,
      selection: { startMs: 12_750, endMs: 14_250, quotedText: "thousand for" },
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    noToolbar();
  });

  it("keeps a note being written through a press outside it, or a new selection", () => {
    const props = renderFinished();
    drag([0, 0], [0, 5]);
    fireEvent.click(within(toolbar()).getByRole("button", { name: "Add note" }));
    const box = screen.getByRole("textbox", { name: "Note" });
    fireEvent.change(box, { target: { value: "half-written thought" } });

    // A press on the transcript: to scroll on a phone, or to look at a line.
    fireEvent.pointerDown(lineText(1));
    fireEvent.pointerUp(lineText(1));
    act(() => {
      vi.advanceTimersByTime(SELECTION_SETTLE_MS);
    });
    expect(screen.getByRole("textbox", { name: "Note" })).toHaveValue("half-written thought");

    // Selecting other text, say to copy it, neither drops it nor offers a bar.
    drag([1, 0], [1, 8]);
    expect(screen.getByRole("textbox", { name: "Note" })).toHaveValue("half-written thought");
    noToolbar();

    fireEvent.keyDown(screen.getByRole("textbox", { name: "Note" }), { key: "Enter" });
    expect(props.onAddNote).toHaveBeenCalledWith(
      expect.objectContaining({ body: "half-written thought", selection: expect.objectContaining({ quotedText: "Forty" }) }),
    );
    // Back to the ⋯ button of the line the text was on, not the top of the page.
    expect(screen.getByRole("button", { name: "Actions for Alex's line at 00:12" })).toHaveFocus();
    // And no bar left over from the text selected while writing.
    noToolbar();
  });

  it("closes an empty note on a press outside it, as a line's note does", () => {
    renderFinished();
    drag([0, 0], [0, 5]);
    fireEvent.click(within(toolbar()).getByRole("button", { name: "Add note" }));
    fireEvent.pointerDown(lineText(1));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers nothing while the mouse is still dragging, however long it rests", () => {
    renderFinished();
    fireEvent.pointerDown(lineText(0));
    const range = document.createRange();
    range.setStart(lineText(0).firstChild!, 0);
    range.setEnd(lineText(0).firstChild!, 5);
    document.getSelection()!.removeAllRanges();
    document.getSelection()!.addRange(range);
    act(() => {
      document.dispatchEvent(new Event("selectionchange"));
      vi.advanceTimersByTime(SELECTION_SETTLE_MS * 4);
    });
    noToolbar();

    fireEvent.pointerUp(lineText(0));
    act(() => {
      vi.advanceTimersByTime(SELECTION_SETTLE_MS);
    });
    expect(toolbar()).toBeInTheDocument();
  });

  it("runs a note on text across two lines from the first word taken to the last", () => {
    const props = renderFinished();
    drag([0, 19], [1, 8]);

    fireEvent.click(within(toolbar()).getByRole("button", { name: "Add note" }));
    const box = screen.getByRole("textbox", { name: "Note" });
    fireEvent.change(box, { target: { value: "Both quotes" } });
    fireEvent.click(screen.getByRole("button", { name: "Save note" }));

    expect(props.onAddNote).toHaveBeenCalledWith(
      expect.objectContaining({
        selection: { startMs: 14_375, endMs: 16_941, quotedText: "the venue Catering" },
      }),
    );
  });

  it("opens no editor on a click on a line's text, whether or not it ends a selection", () => {
    renderFinished();
    drag([0, 6], [0, 14]);
    fireEvent.click(lineText(0));

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(toolbar()).toBeInTheDocument();

    // Edit text in the line's ⋯ menu is the only way into the editor.
    document.getSelection()!.removeAllRanges();
    fireEvent.click(lineText(0));
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("goes away on Escape, or on a press anywhere but the toolbar", () => {
    renderFinished();
    drag([0, 0], [0, 5]);
    fireEvent.pointerDown(toolbar());
    expect(toolbar()).toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: "Escape" });
    noToolbar();

    drag([0, 0], [0, 5]);
    fireEvent.pointerDown(document.body);
    noToolbar();
  });

  it("copies the selected text and goes away", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    try {
      renderFinished();
      drag([0, 6], [1, 8]);
      await act(async () => {
        fireEvent.click(within(toolbar()).getByRole("button", { name: "Copy" }));
      });

      expect(writeText).toHaveBeenCalledWith(document.getSelection()!.toString());
      expect(writeText.mock.calls[0]).toEqual([expect.stringContaining("thousand for the venue")]);
      noToolbar();
    } finally {
      Reflect.deleteProperty(navigator, "clipboard");
    }
  });

  it("offers only Copy for a selection of spaces, and nothing for text outside the lines", () => {
    renderFinished();
    drag([0, 5], [0, 6]);
    expect(within(toolbar()).getAllByRole("button").map((button) => button.textContent)).toEqual(["Copy"]);

    const name = screen.getByRole("button", { name: "Alex" });
    fireEvent.pointerDown(name);
    const range = document.createRange();
    range.selectNodeContents(name);
    document.getSelection()!.removeAllRanges();
    document.getSelection()!.addRange(range);
    fireEvent.pointerUp(name);
    act(() => {
      document.dispatchEvent(new Event("selectionchange"));
      vi.advanceTimersByTime(SELECTION_SETTLE_MS);
    });
    expect(document.getSelection()!.toString()).toBe("Alex");
    noToolbar();
  });

  it("shows nothing where notes can't be added", () => {
    renderFinished({ onAddNote: undefined });
    drag([0, 6], [0, 14]);
    noToolbar();
  });
});

describe("selected text in the live transcript", () => {
  it("hands the selected words to the note box", () => {
    const onNoteOnLine = vi.fn();
    render(<LiveTranscriptPanel segments={SEGMENTS} hasLoaded onNoteOnLine={onNoteOnLine} />);
    drag([0, 6], [0, 18]);

    fireEvent.click(within(toolbar()).getByRole("button", { name: "Add note" }));

    expect(onNoteOnLine).toHaveBeenCalledWith({ startMs: 12_750, endMs: 14_250, quotedText: "thousand for" });
    noToolbar();
  });

  it("keeps the words as selected when the line is rewritten under them", () => {
    const onNoteOnLine = vi.fn();
    const { rerender } = render(
      <LiveTranscriptPanel segments={SEGMENTS} hasLoaded onNoteOnLine={onNoteOnLine} />,
    );
    drag([0, 6], [0, 18]);

    const rewritten = [{ ...SEGMENTS[0], text: "Fourteen thousand for the venue, then" }, SEGMENTS[1]];
    rerender(<LiveTranscriptPanel segments={rewritten} hasLoaded onNoteOnLine={onNoteOnLine} />);
    // The rewrite drops what the page had selected.
    expect(document.getSelection()!.toString()).not.toBe("thousand for");
    act(() => {
      document.dispatchEvent(new Event("selectionchange"));
      vi.advanceTimersByTime(SELECTION_SETTLE_MS);
    });
    fireEvent.click(within(toolbar()).getByRole("button", { name: "Add note" }));

    expect(onNoteOnLine).toHaveBeenCalledWith({ startMs: 12_750, endMs: 14_250, quotedText: "thousand for" });
  });

  it("shows nothing without a note box", () => {
    render(<LiveTranscriptPanel segments={SEGMENTS} hasLoaded />);
    drag([0, 6], [0, 18]);
    noToolbar();
  });
});
