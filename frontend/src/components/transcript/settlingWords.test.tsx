import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/test/SharedLiveTranscript";
import type { TranscriptSegment } from "@/types";
import { SELECTION_SETTLE_MS } from "./_hooks/useCapturedSelection";
import TranscriptLineText from "./TranscriptLineText";

const line = (): TranscriptSegment => ({
  id: "open", revision: 3, start: 1, end: 3, text: "Firm words pending", speaker: "UNKNOWN",
  settling_required: true, provisional: true, locked: false,
  words: [
    { id: "w1", order: 0, word: "Firm", start_ms: 1000, end_ms: 1200, locked: true },
    { id: "w2", order: 1, word: " words", start_ms: 1500, end_ms: 1700, locked: true },
    { id: "w3", order: 2, word: " pending", start_ms: 2500, end_ms: 3000, locked: false },
  ],
});

afterEach(() => {
  document.getSelection()?.removeAllRanges();
  vi.useRealTimers();
});

function selectWords(first: string, last: string) {
  const start = document.querySelector(`[data-word-id="${first}"]`)!;
  const end = document.querySelector(`[data-word-id="${last}"]`)!;
  const range = document.createRange();
  range.setStart(start.firstChild!, 0);
  range.setEnd(end.firstChild!, end.textContent!.length);
  const selection = document.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  act(() => {
    document.dispatchEvent(new Event("selectionchange"));
    vi.advanceTimersByTime(SELECTION_SETTLE_MS);
  });
}

describe("words in an open settled line", () => {
  it("dims only unlocked words and updates the same line and word nodes when they lock", () => {
    const segment = line();
    const view = render(<LiveTranscriptPanel segments={[segment]} hasLoaded />);
    const paragraph = view.container.querySelector("p[data-segment-id]")!;
    const locked = paragraph.querySelector('[data-word-id="w1"]')!;
    const pending = paragraph.querySelector('[data-word-id="w3"]')!;
    expect(paragraph).toHaveTextContent("Firm words pending");
    expect(paragraph).not.toHaveClass("italic");
    expect(locked).toHaveClass("not-italic");
    expect(locked).not.toHaveClass("text-contrast-helper");
    expect(pending).toHaveClass("italic", "text-contrast-helper");
    view.rerender(<LiveTranscriptPanel segments={[{ ...segment, revision: 4,
      words: segment.words!.map((word) => ({ ...word, locked: true })) }]} hasLoaded />);
    expect(view.container.querySelector("p[data-segment-id]")).toBe(paragraph);
    expect(paragraph.querySelector('[data-word-id="w3"]')).toBe(pending);
    expect(pending).toHaveClass("not-italic");
    expect(pending).not.toHaveClass("italic", "text-contrast-helper");
  });

  it("allows locked words in an open line using their real times, across later updates", () => {
    vi.useFakeTimers();
    const onNote = vi.fn();
    const segment = line();
    const view = render(<LiveTranscriptPanel segments={[segment]} hasLoaded onNoteOnLine={onNote} />);
    selectWords("w1", "w2");
    const add = within(screen.getByRole("toolbar")).getByRole("button", { name: "Add note" });
    expect(add).toBeEnabled();
    view.rerender(<LiveTranscriptPanel segments={[{ ...segment, revision: 4 }]} hasLoaded onNoteOnLine={onNote} />);
    expect(add).toBeEnabled();
    fireEvent.click(add);
    expect(onNote).toHaveBeenCalledWith({ startMs: 1000, endMs: 1700, quotedText: "Firm words" });
  });

  it("keeps selection and Copy available but disables a note containing an unlocked word", () => {
    vi.useFakeTimers();
    const onNote = vi.fn();
    render(<LiveTranscriptPanel segments={[line()]} hasLoaded onNoteOnLine={onNote} />);
    selectWords("w2", "w3");
    expect(document.getSelection()!.toString()).toBe("words pending");
    const toolbar = screen.getByRole("toolbar");
    const add = within(toolbar).getByRole("button", { name: "Add note" });
    expect(add).toBeDisabled();
    expect(add).toHaveAttribute("title", "Selected words are still settling.");
    expect(within(toolbar).getByRole("button", { name: "Copy" })).toBeEnabled();
    fireEvent.click(add);
    expect(onNote).not.toHaveBeenCalled();
  });

  it("does not enable a captured quote when a pending word changes wording under the same ID", () => {
    vi.useFakeTimers();
    const onNote = vi.fn();
    const segment = line();
    const view = render(<LiveTranscriptPanel segments={[segment]} hasLoaded onNoteOnLine={onNote} />);
    selectWords("w3", "w3");
    const add = screen.getByRole("button", { name: "Add note" });
    expect(add).toBeDisabled();
    view.rerender(<LiveTranscriptPanel segments={[{ ...segment, revision: 4,
      text: "Firm words corrected", words: segment.words!.map((word) => word.id === "w3"
        ? { ...word, word: " corrected", locked: true } : word) }]} hasLoaded onNoteOnLine={onNote} />);
    expect(add).toBeDisabled();
    fireEvent.click(add);
    expect(onNote).not.toHaveBeenCalled();
  });

  it("enables an unchanged pending selection once its words lock while the line stays open", () => {
    vi.useFakeTimers();
    const onNote = vi.fn();
    const segment = line();
    const view = render(<LiveTranscriptPanel segments={[segment]} hasLoaded onNoteOnLine={onNote} />);
    selectWords("w3", "w3");
    const add = screen.getByRole("button", { name: "Add note" });
    expect(add).toBeDisabled();
    view.rerender(<LiveTranscriptPanel segments={[{ ...segment, revision: 4,
      words: segment.words!.map((word) => ({ ...word, locked: true })) }]} hasLoaded onNoteOnLine={onNote} />);
    expect(add).toBeEnabled();
    fireEvent.click(add);
    expect(onNote).toHaveBeenCalledWith(expect.objectContaining({ quotedText: "pending" }));
  });

  it("keeps unmarked rendering and interpolated selection unchanged even with word metadata", () => {
    vi.useFakeTimers();
    const onNote = vi.fn();
    const segment = { ...line(), settling_required: undefined };
    const view = render(<LiveTranscriptPanel segments={[segment]} hasLoaded onNoteOnLine={onNote} />);
    const paragraph = view.container.querySelector("p[data-segment-id]")!;
    expect(paragraph).toHaveClass("italic", "text-contrast-helper");
    expect(paragraph.querySelector("[data-word-id]")).toBeNull();
    const range = document.createRange();
    range.setStart(paragraph.firstChild!, 0);
    range.setEnd(paragraph.firstChild!, 4);
    document.getSelection()!.addRange(range);
    act(() => {
      document.dispatchEvent(new Event("selectionchange"));
      vi.advanceTimersByTime(SELECTION_SETTLE_MS);
    });
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    expect(onNote).toHaveBeenCalledWith({ startMs: 1000, endMs: 1444, quotedText: "Firm" });
  });

  it("preserves exact text, punctuation, untimed atoms and decorations in the shared finished renderer", () => {
    const words = [
      { id: "a", order: 0, word: "Hello", start_ms: 0, end_ms: 400, locked: true },
      { id: "b", order: 1, word: ",", join_left: true, start_ms: 400, end_ms: 400, locked: true },
      { id: "c", order: 2, word: " corrected phrase", start_ms: null, end_ms: null, locked: false },
    ];
    const view = render(<TranscriptLineText text="Hello, corrected phrase" segmentId="p" startMs={0} endMs={2000}
      settlingRequired words={words} settling className="text-contrast-muted"
      decorations={[{ kind: "search", from: 3, to: 10, current: true }]} />);
    const paragraph = view.container.querySelector("p")!;
    expect(paragraph.textContent).toBe("Hello, corrected phrase");
    expect(Array.from(paragraph.querySelectorAll("mark")).map((mark) => mark.textContent).join("")).toBe("lo, cor");
    expect(paragraph.querySelector('[data-word-id="c"]')).toHaveClass("italic", "text-contrast-helper");
  });

  it("keeps zero-width search matches at word boundaries and the end of marked text", () => {
    const segment = line();
    const view = render(<TranscriptLineText text={segment.text} segmentId="p" startMs={1000} endMs={3000}
      settlingRequired words={segment.words} className="text-contrast-muted"
      decorations={[4, 5, segment.text.length].map((from) => ({ kind: "search", from, to: from, current: true }))} />);
    const paragraph = view.container.querySelector("p")!;
    expect(paragraph.textContent).toBe(segment.text);
    expect(paragraph.querySelectorAll("mark")).toHaveLength(3);
  });

  it("keeps a mixed untimed selection's whole fallback span", () => {
    vi.useFakeTimers();
    const onNote = vi.fn();
    const segment = line();
    segment.words = segment.words!.map((word) => word.id === "w2"
      ? { ...word, start_ms: null, end_ms: null, locked: true } : { ...word, locked: true });
    render(<LiveTranscriptPanel segments={[segment]} hasLoaded onNoteOnLine={onNote} />);
    selectWords("w2", "w3");
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    expect(onNote).toHaveBeenCalledWith({ startMs: 1000, endMs: 3000, quotedText: "words pending" });
  });

  it("can quote locked zero-duration punctuation without including the pending neighbour", () => {
    vi.useFakeTimers();
    const onNote = vi.fn();
    const segment = line();
    segment.text = "Firm, pending";
    segment.words![1] = { ...segment.words![1], word: ",", join_left: true, start_ms: 1200, end_ms: 1200 };
    render(<LiveTranscriptPanel segments={[segment]} hasLoaded onNoteOnLine={onNote} />);
    selectWords("w2", "w2");
    const add = screen.getByRole("button", { name: "Add note" });
    expect(add).toBeEnabled();
    fireEvent.click(add);
    expect(onNote).toHaveBeenCalledWith({ startMs: 1200, endMs: 1201, quotedText: "," });
  });
});
