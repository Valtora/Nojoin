import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import TranscriptView from "../TranscriptView";
import { linePicksOfRange, selectionFromPicks } from "@/lib/notes/selectionRange";
import { pendingTailLines } from "./pendingTailFixtures";
import { viewProps } from "./transcriptTestFixtures";
import { SELECTION_SETTLE_MS } from "./_hooks/useCapturedSelection";
import { LONG_PRESS_MS } from "./TranscriptActionsMenu";

afterEach(() => {
  document.getSelection()?.removeAllRanges();
  vi.useRealTimers();
});

function rangeOf(first: string, last: string) {
  const start = document.querySelector(`[data-word-id="${first}"]`)!;
  const end = document.querySelector(`[data-word-id="${last}"]`)!;
  const range = document.createRange();
  range.setStart(start.firstChild!, 0);
  range.setEnd(end.firstChild!, end.textContent!.length);
  return range;
}

describe("source targets inside a shared pending bubble", () => {
  it("quotes selected words across the visual merge", () => {
    const { container } = render(<TranscriptView {...viewProps(pendingTailLines())} live={{ hasLoaded: true }} />);
    expect(container.querySelectorAll('[data-ui-reading="bubble"]')).toHaveLength(1);
    const picks = linePicksOfRange(rangeOf("w2", "w3"), container);
    expect(selectionFromPicks(picks)).toEqual({ startMs: 500, endMs: 1500, quotedText: "words pending" });
    expect(selectionFromPicks(linePicksOfRange(rangeOf("w3", "w4"), container))).toEqual({
      startMs: 1000, endMs: 2000, quotedText: "pending tail" });
  });

  it("keeps Copy and the captured original selection while enabling a note only when its words lock", () => {
    vi.useFakeTimers();
    const [anchor, tail] = pendingTailLines();
    const onNote = vi.fn();
    const presentation = { hasLoaded: true, onNoteOnLine: onNote };
    const props = viewProps([anchor, tail]);
    const { rerender } = render(<TranscriptView {...props} live={presentation} />);
    const selection = document.getSelection()!;
    selection.addRange(rangeOf("w2", "w3"));
    act(() => {
      document.dispatchEvent(new Event("selectionchange"));
      vi.advanceTimersByTime(SELECTION_SETTLE_MS);
    });
    const toolbar = screen.getByRole("toolbar", { name: "Selected text" });
    const add = within(toolbar).getByRole("button", { name: "Add note" });
    expect(add).toBeDisabled();
    expect(within(toolbar).getByRole("button", { name: "Copy" })).toBeEnabled();
    rerender(<TranscriptView {...props} segments={[anchor, { ...tail, revision: 5,
      words: tail.words!.map((word) => ({ ...word, locked: true })) }]} live={presentation} />);
    expect(add).toBeEnabled();
    fireEvent.click(add);
    expect(onNote).toHaveBeenCalledWith({ startMs: 500, endMs: 1500, quotedText: "words pending" });
  });

  it("keeps separate whole-line note targets for anchor and pending inside one box", () => {
    const onNote = vi.fn();
    const locked = pendingTailLines().map((line) => ({ ...line, locked: true }));
    render(<TranscriptView {...viewProps(locked)} live={{ hasLoaded: true, onNoteOnLine: onNote }} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:01" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    expect(onNote).toHaveBeenLastCalledWith(expect.objectContaining({ startMs: 1000, endMs: 2000 }));
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    expect(onNote).toHaveBeenLastCalledWith(expect.objectContaining({ startMs: 0, endMs: 1000 }));
  });

  it("opens only the pending source menu on a touch long press inside the shared bubble", () => {
    vi.useFakeTimers();
    const onNote = vi.fn();
    const locked = pendingTailLines().map((line) => ({ ...line, locked: true }));
    render(<TranscriptView {...viewProps(locked)} live={{ hasLoaded: true, onNoteOnLine: onNote }} />);
    fireEvent.pointerDown(screen.getByRole("button", { name: "Actions for the line at 00:01" }), {
      pointerType: "touch", isPrimary: true, clientX: 10, clientY: 10,
    });
    act(() => vi.advanceTimersByTime(LONG_PRESS_MS));
    expect(screen.getAllByRole("menu")).toHaveLength(1);
    const menu = screen.getByRole("menu", { name: "Actions for the line at 00:01" });
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Note on this line" }));
    expect(onNote).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ startMs: 1000, endMs: 2000 }));
  });

  it("quotes punctuation attached across inline source boundaries without inventing a space", () => {
    const [anchor, tail] = pendingTailLines();
    anchor.text = "Hi";
    anchor.words = [{ ...anchor.words![0], word: "Hi" }];
    tail.text = ",";
    tail.words = [{ ...tail.words![0], word: ",", join_left: true }];
    const { container } = render(<TranscriptView {...viewProps([anchor, tail])} live={{ hasLoaded: true }} />);
    const range = rangeOf("w1", "w3");
    expect(range.toString()).toBe("Hi,");
    expect(selectionFromPicks(linePicksOfRange(range, container))).toEqual({
      startMs: 0, endMs: 1500, quotedText: "Hi," });
  });

  it("retains pending search matches against the pending fragment's own offsets", () => {
    const { container } = render(<TranscriptView {...viewProps(pendingTailLines())} live={{ hasLoaded: true }} />);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.change(screen.getByPlaceholderText("Find..."), { target: { value: "pending" } });
    const mark = container.querySelector("mark")!;
    expect(mark).toHaveTextContent("pending");
    expect(mark.closest("p")).toHaveAttribute("data-segment-id", "tail");
  });
});
