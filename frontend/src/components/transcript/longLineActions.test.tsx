import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TranscriptView from "../TranscriptView";
import LiveTranscriptPanel from "../LiveTranscriptPanel";
import type { TranscriptSegment } from "@/types";
import { line, viewProps } from "./transcriptTestFixtures";

const longLine = (marked = true): TranscriptSegment => {
  const words = Array.from({ length: 30 }, (_, i) => ({
    id: `w${i}`, order: i, word: `word${i}`, start_ms: i * 1000, end_ms: (i + 1) * 1000, locked: true,
  }));
  return line({ words, text: words.map((word) => word.word).join(" "), settling_required: marked || undefined });
};
const open = (action: string) => {
  fireEvent.click(screen.getByRole("button", { name: "Actions for Alex's line at 00:00" }));
  fireEvent.click(screen.getByRole("menuitem", { name: action }));
};

describe("actions on a thirty second line", () => {
  it("plays the full bounded line, plays on from the menu, edits the tail and finds a tail match", async () => {
    const segment = longLine();
    const props = viewProps([segment]);
    const { container, rerender } = render(<TranscriptView {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Play Alex at 00:00" }));
    expect(props.onPlaySegment).toHaveBeenCalledWith(0, 30);
    rerender(<TranscriptView {...props} currentTime={29} />);
    open("Play from here");
    expect(props.onPlaySegment).toHaveBeenLastCalledWith(0);
    expect(container.querySelector("[data-ui-reading='bubble']")).toHaveClass("bg-status-success-bg");
    open("Edit text");
    const editor = screen.getByRole("textbox");
    expect(editor).toHaveValue(segment.text);
    const revised = segment.text.replace("word29", "lastword");
    fireEvent.change(editor, { target: { value: revised } });
    fireEvent.keyDown(editor, { key: "Enter" });
    await waitFor(() => expect(props.onUpdateSegmentText).toHaveBeenCalledWith(segment, revised));
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.change(screen.getByPlaceholderText("Find..."), { target: { value: "word29" } });
    expect(container.querySelector("mark")).toHaveTextContent("word29");
  });

  it.each([false, true])("notes the full 30 s span with the existing quote policy (marked=%s)", (marked) => {
    const segment = longLine(marked);
    const props = { ...viewProps([segment]), onAddNote: vi.fn(() => null) };
    render(<TranscriptView {...props} />);
    open("Note on this line");
    const box = screen.getByRole("textbox", { name: "Note" });
    fireEvent.change(box, { target: { value: "Whole turn" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(props.onAddNote).toHaveBeenCalledWith(marked
      ? { kind: "point", body: "Whole turn", anchorMs: 0, span: { startMs: 0, endMs: 30000 } }
      : { kind: "selection", body: "Whole turn", anchorMs: null,
          selection: { startMs: 0, endMs: 30000, quotedText: segment.text } });
  });

  it.each([false, true])("splits a finished line at a genuine late word before narrower speaker assignment (marked=%s)", async (marked) => {
    const segment = longLine(marked);
    const split = vi.fn().mockResolvedValue(true);
    render(<TranscriptView {...viewProps([segment])} onSplitSegment={split} />);
    open("Split line");
    const picker = screen.getByRole("combobox", { name: "Split before word" });
    expect(screen.getAllByRole("option")).toHaveLength(29);
    fireEvent.change(picker, { target: { value: "w25" } });
    fireEvent.click(screen.getByRole("button", { name: "Split" }));
    await waitFor(() => expect(split).toHaveBeenCalledWith(segment, "w25"));
    expect(screen.getByRole("button", { name: "Actions for Alex's line at 00:00" })).toHaveFocus();
  });

  it("does not offer a fabricated split boundary after manually replacing text", () => {
    render(<TranscriptView {...viewProps([line({ words: [] })])} onSplitSegment={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for Alex's line at 00:00" }));
    expect(screen.queryByRole("menuitem", { name: "Split line" })).toBeNull();
  });

  it("does not offer a split for legacy raw ASR words without canonical identities or millisecond times", () => {
    const words = [{ word: "Opening", start: 0, end: 15 }, { word: "ending", start: 15, end: 30 }];
    const legacy = line({ words: words as unknown as TranscriptSegment["words"], text: "Opening ending" });
    render(<TranscriptView {...viewProps([legacy])} onSplitSegment={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for Alex's line at 00:00" }));
    expect(screen.queryByRole("menuitem", { name: "Split line" })).toBeNull();
  });

  it("submits a split against the revision shown when its picker opened", async () => {
    const original = longLine();
    const split = vi.fn().mockResolvedValue(false);
    const props = viewProps([original]);
    const { rerender } = render(<TranscriptView {...props} onSplitSegment={split} />);
    open("Split line");
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "w25" } });
    rerender(<TranscriptView {...props} segments={[{ ...original, revision: 5 }]} onSplitSegment={split} />);
    fireEvent.click(screen.getByRole("button", { name: "Split" }));
    await waitFor(() => expect(split).toHaveBeenCalledWith(original, "w25"));
    expect(screen.getByRole("combobox")).toHaveValue("w25");
  });

  it("leaves an internal timeline note after its containing long line and before the next turn", () => {
    const props = viewProps([longLine(), line({ id: "next", start: 31, end: 32, text: "Next turn" })]);
    const { container } = render(<TranscriptView {...props} timelineNotes={[{
      id: "n1", revision: 1, recording_id: "r1", kind: "point", anchor_ms: 15000, body: "Internal reminder",
      created_at: "2026-10-27T00:00:00Z", updated_at: "2026-10-27T00:00:00Z",
    }]} />);
    const text = container.textContent!;
    expect(text.indexOf("word29")).toBeLessThan(text.indexOf("Internal reminder"));
    expect(text.indexOf("Internal reminder")).toBeLessThan(text.indexOf("Next turn"));
    expect(screen.getByText("00:15")).toBeInTheDocument();
  });

  it("highlights a selection note on the last words of a long line", () => {
    const { container } = render(<TranscriptView {...viewProps([longLine()])} timelineNotes={[{
      id: "tail-note", revision: 1, recording_id: "r1", kind: "selection", start_ms: 28000, end_ms: 30000,
      quoted_text: "word28 word29", body: "Check the ending",
      created_at: "2026-10-27T00:00:00Z", updated_at: "2026-10-27T00:00:00Z",
    }]} />);
    const marked = Array.from(container.querySelectorAll("mark[data-note-id='tail-note']"))
      .map((mark) => mark.textContent).join(" ");
    expect(marked).toContain("word28");
    expect(marked).toContain("word29");
    expect(marked).not.toContain("word27");
  });

  it("live long lines keep late-word splitting and full-span timeline notes", () => {
    const segment = { ...longLine(), locked: true, provisional: true };
    const note = vi.fn();
    const handlers = { split: vi.fn(), editText: vi.fn(), changeSpeaker: vi.fn() };
    render(<LiveTranscriptPanel segments={[segment]} hasLoaded onNoteOnLine={note} lineHandlers={handlers} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    expect(note).toHaveBeenCalledWith({ startMs: 0, endMs: 30000 });
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Split line" }));
    expect(within(screen.getByRole("combobox")).getAllByRole("option").at(-1)).toHaveTextContent("word29");
  });
});
