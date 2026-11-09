import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/test/SharedLiveTranscript";
import type { ComposerDraft, InlineNote, NoteSelection, OutboxDraft } from "@/lib/notes/noteOutbox";
import type { TranscriptSegment } from "@/types";

import LiveNoteComposer from "./LiveNoteComposer";

const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "UNKNOWN", start: 1, end: 3, text: "Good morning" },
  { id: "u1", speaker: "UNKNOWN", start: 12, end: 15.5, text: "Forty thousand for the venue" },
  { id: "u2", speaker: "UNKNOWN", start: 16, end: 16, text: "" },
];

const ON_LINE: NoteSelection = { startMs: 12_000, endMs: 15_500, quotedText: "Forty thousand for the venue" };

describe("the live view's ⋯ menu", () => {
  it("offers only a note on the line, which hands the line's span and text over", () => {
    const onNoteOnLine = vi.fn();
    render(<LiveTranscriptPanel segments={SEGMENTS} hasLoaded onNoteOnLine={onNoteOnLine} />);

    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:12" }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Note on this line",
    ]);
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Note on this line" }));

    expect(onNoteOnLine).toHaveBeenCalledWith(ON_LINE);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("puts no menu on a line with nothing to quote, nor anywhere without a note box", () => {
    const { unmount } = render(
      <LiveTranscriptPanel segments={SEGMENTS} hasLoaded onNoteOnLine={vi.fn()} />,
    );
    expect(screen.getAllByRole("button", { name: /^Actions for the line/ })).toHaveLength(2);
    unmount();

    render(<LiveTranscriptPanel segments={SEGMENTS} hasLoaded />);
    expect(screen.queryByRole("button", { name: /^Actions for the line/ })).not.toBeInTheDocument();
  });

  it("shows a note on a line with the words it quotes", () => {
    const note: InlineNote = {
      key: "n1",
      id: "n1",
      kind: "selection",
      body: "Check the figure",
      start_ms: 12_000,
      end_ms: 15_500,
      quoted_text: "Forty thousand",
      created_at: "2026-10-22T10:00:00Z",
      status: "saved",
      error: null,
    };
    render(<LiveTranscriptPanel segments={SEGMENTS} hasLoaded notes={[note]} />);

    const line = screen.getByTestId("timeline-note");
    expect(within(line).getByTestId("timeline-note-quote")).toHaveTextContent("“Forty thousand”");
    expect(line).toHaveTextContent("Check the figure");
  });
});

/** The note box with its draft kept above it, as the recording page keeps it. */
function ComposerOnLine({
  initial,
  onSubmit,
}: {
  initial: ComposerDraft;
  onSubmit: (note: OutboxDraft) => string | null;
}) {
  const [draft, setDraft] = useState(initial);
  return (
    <>
      <LiveNoteComposer readAudioNow={() => 99_000} onSubmit={onSubmit} draft={draft} onDraftChange={setDraft} />
      <output data-testid="draft">{JSON.stringify(draft)}</output>
    </>
  );
}

const draftNow = (): ComposerDraft => JSON.parse(screen.getByTestId("draft").textContent ?? "{}");

describe("the note box on a chosen line", () => {
  const renderOnLine = (mode: ComposerDraft["mode"] = "typing") => {
    const onSubmit = vi.fn<(note: OutboxDraft) => string | null>(() => null);
    render(
      <ComposerOnLine
        initial={{ text: "", mode, typingStartMs: null, selection: ON_LINE }}
        onSubmit={onSubmit}
      />,
    );
    return { onSubmit, box: screen.getByRole("textbox", { name: "New note" }) };
  };

  it("shows the quote over the box, and no other place", () => {
    const { box } = renderOnLine("head");

    const chip = screen.getByTestId("note-selection-chip");
    expect(chip).toHaveTextContent("00:12 “Forty thousand for the venue”");
    expect(screen.queryByTestId("note-placement-chip")).toBeNull();
    expect(box.getAttribute("aria-describedby")).toContain(chip.id);
    expect(screen.getByTestId("live-note-status")).toHaveTextContent(/^$/);
  });

  it("sends a note on the line, then goes back to where typing starts", () => {
    const { onSubmit, box } = renderOnLine("typing");
    fireEvent.change(box, { target: { value: "Check the figure" } });
    fireEvent.keyDown(box, { key: "Enter" });

    expect(onSubmit).toHaveBeenCalledWith({
      kind: "selection",
      body: "Check the figure",
      anchorMs: null,
      selection: ON_LINE,
    });
    expect(screen.getByTestId("live-note-status")).toHaveTextContent("Note added at 00:12.");
    expect(screen.queryByTestId("note-selection-chip")).not.toBeInTheDocument();
    expect(screen.queryByTestId("note-placement-chip")).not.toBeInTheDocument();
    expect(draftNow()).toMatchObject({ text: "", mode: "typing", typingStartMs: null });
    expect(draftNow().selection ?? null).toBeNull();
  });

  it("takes the line off with the chip's button, keeping the text and the place before it", () => {
    const { box } = renderOnLine("tail");
    fireEvent.change(box, { target: { value: "Keep me" } });
    fireEvent.click(screen.getByRole("button", { name: "Don't put the note on this text" }));

    expect(screen.queryByTestId("note-selection-chip")).not.toBeInTheDocument();
    expect(screen.getByTestId("note-placement-chip")).toHaveTextContent("After the transcript");
    expect(draftNow()).toMatchObject({ text: "Keep me", mode: "tail", selection: null });
    expect(box).toHaveFocus();
  });

  it("takes the line off when the whole meeting is chosen instead", () => {
    renderOnLine("typing");
    fireEvent.click(screen.getByRole("button", { name: "Note on the whole meeting" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Before the transcript" }));

    expect(screen.queryByTestId("note-selection-chip")).not.toBeInTheDocument();
    expect(screen.getByTestId("note-placement-chip")).toHaveTextContent("Before the transcript");
    expect(draftNow()).toMatchObject({ mode: "head", selection: null });
  });
});
