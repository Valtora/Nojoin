import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/components/LiveTranscriptPanel";
import type { TranscriptSegment } from "@/types";
import { SELECTION_SETTLE_MS } from "./_hooks/useCapturedSelection";

const segment = (id: string, start: number, locked: boolean): TranscriptSegment => ({
  id, start, end: start + 1, text: "aWord", speaker: "UNKNOWN", revision: 1,
  settling_required: true, provisional: true, locked,
  words: [{ id: `${id}-w`, order: 0, word: "aWord", start_ms: start * 1000,
    end_ms: (start + 1) * 1000, locked }],
});

function selectWords(first: string, last: string, partial = false) {
  const start = document.querySelector(`[data-word-id="${first}"]`)!;
  const end = document.querySelector(`[data-word-id="${last}"]`)!;
  const range = document.createRange();
  range.setStart(start.firstChild!, partial ? 1 : 0);
  range.setEnd(end.firstChild!, partial ? 3 : 5);
  const selection = document.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  act(() => {
    document.dispatchEvent(new Event("selectionchange"));
    vi.advanceTimersByTime(SELECTION_SETTLE_MS);
  });
}

afterEach(() => {
  document.getSelection()?.removeAllRanges();
  vi.useRealTimers();
});

it("captures complete word evidence for partial text across open and locked lines", () => {
  vi.useFakeTimers();
  const onNote = vi.fn();
  const open = { ...segment("a", 1, true), locked: false };
  const locked = segment("b", 3, true);
  render(<LiveTranscriptPanel hasLoaded segments={[open, locked]} onNoteOnLine={onNote} />);

  selectWords("a-w", "b-w", true);
  expect(screen.getByRole("button", { name: "Add note" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Add note" }));

  expect(onNote).toHaveBeenCalledWith({
    startMs: 1000, endMs: 4000, quotedText: "Word aWo",
    sourceUtterances: [
      { id: "a", revision: 1, word_ids: ["a-w"], word_texts: { "a-w": "aWord" } },
      { id: "b", revision: 1, word_ids: ["b-w"], word_texts: { "b-w": "aWord" } },
    ],
  });
});

it("retains captured wording and provisional times after an unchanged word locks at new times", () => {
  vi.useFakeTimers();
  const onNote = vi.fn();
  const pending = segment("a", 1, false);
  const view = render(<LiveTranscriptPanel hasLoaded segments={[pending]} onNoteOnLine={onNote} />);

  selectWords("a-w", "a-w");
  expect(screen.getByRole("button", { name: "Add note" })).toBeDisabled();
  view.rerender(<LiveTranscriptPanel hasLoaded segments={[{
    ...pending, start: 3, end: 4, revision: 2, locked: true,
    words: [{ ...pending.words![0], locked: true, start_ms: 3000, end_ms: 4000 }],
  }]} onNoteOnLine={onNote} />);
  fireEvent.click(screen.getByRole("button", { name: "Add note" }));

  expect(onNote).toHaveBeenCalledWith({
    startMs: 1000, endMs: 2000, quotedText: "aWord",
    sourceUtterances: [{ id: "a", revision: 1, word_ids: ["a-w"],
      word_texts: { "a-w": "aWord" } }],
  });
});
