import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OutboxDraft } from "@/lib/notes/noteOutbox";

import LiveNoteComposer from "./LiveNoteComposer";

/** The recording's position on its audio timeline, moved by each test. */
let audioNowMs = 0;
const readAudioNow = vi.fn(() => audioNowMs);
const onSubmit = vi.fn<(draft: OutboxDraft) => string | null>(() => null);

const renderComposer = () => {
  render(<LiveNoteComposer readAudioNow={readAudioNow} onSubmit={onSubmit} />);
  return screen.getByRole("textbox", { name: "New note" }) as HTMLTextAreaElement;
};

const type = (box: HTMLTextAreaElement, value: string) =>
  fireEvent.change(box, { target: { value } });

const pressEnter = (box: HTMLTextAreaElement, init: KeyboardEventInit & { keyCode?: number } = {}) =>
  fireEvent.keyDown(box, { key: "Enter", code: "Enter", ...init });

describe("LiveNoteComposer", () => {
  beforeEach(() => {
    audioNowMs = 0;
    readAudioNow.mockClear();
    onSubmit.mockReset();
    onSubmit.mockReturnValue(null);
  });

  it("anchors a note where the recording was at the first keystroke, however long it takes to write", () => {
    const box = renderComposer();

    audioNowMs = 61_000;
    type(box, "B");
    audioNowMs = 95_000;
    type(box, "Budget agreed, pending legal");
    pressEnter(box);

    expect(onSubmit).toHaveBeenCalledWith({
      kind: "point",
      body: "Budget agreed, pending legal",
      anchorMs: 61_000,
    });
    expect(box.value).toBe("");
    expect(screen.getByText("Placed where you start typing. Shift+Enter starts a new line.")).toBeInTheDocument();
  });

  it("drops the anchor when the box is emptied, and takes a new one on the next keystroke", () => {
    const box = renderComposer();

    audioNowMs = 10_000;
    type(box, "Wrong");
    expect(screen.getByText("Placed at 00:10, where you started typing.")).toBeInTheDocument();
    type(box, "");
    expect(screen.getByText("Placed where you start typing. Shift+Enter starts a new line.")).toBeInTheDocument();
    audioNowMs = 40_000;
    type(box, "  ");
    expect(readAudioNow).toHaveBeenCalledTimes(1);
    audioNowMs = 42_000;
    type(box, "  Right");
    pressEnter(box);

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ anchorMs: 42_000 }));
  });

  it("anchors at send in that mode, and leaves meeting-wide notes unanchored", () => {
    const box = renderComposer();
    const mode = screen.getByRole("combobox", { name: "Place note" });

    fireEvent.change(mode, { target: { value: "send" } });
    audioNowMs = 1_000;
    type(box, "At send");
    audioNowMs = 9_000;
    pressEnter(box);
    expect(onSubmit).toHaveBeenLastCalledWith({ kind: "point", body: "At send", anchorMs: 9_000 });

    fireEvent.change(mode, { target: { value: "head" } });
    type(box, "Agenda");
    pressEnter(box);
    expect(onSubmit).toHaveBeenLastCalledWith({ kind: "head", body: "Agenda", anchorMs: null });

    fireEvent.change(mode, { target: { value: "tail" } });
    type(box, "Follow up Friday");
    pressEnter(box);
    expect(onSubmit).toHaveBeenLastCalledWith({ kind: "tail", body: "Follow up Friday", anchorMs: null });
  });

  it("never sends on an Enter that belongs to an input method's composition", () => {
    const box = renderComposer();
    type(box, "にほんご");

    pressEnter(box, { isComposing: true });
    // Safari ends the composition first and reports the Enter as keyCode 229.
    pressEnter(box, { keyCode: 229 });

    expect(onSubmit).not.toHaveBeenCalled();
    expect(box.value).toBe("にほんご");

    pressEnter(box);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("starts a new line on Shift+Enter instead of sending", () => {
    const box = renderComposer();
    type(box, "First line");

    const notPrevented = pressEnter(box, { shiftKey: true });

    expect(notPrevented).toBe(true);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("does nothing on Enter in an empty box", () => {
    const box = renderComposer();
    type(box, "   ");

    pressEnter(box);

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("keeps the text and its anchor when the note is refused before sending", () => {
    onSubmit.mockReturnValueOnce("A note can be at most 50,000 characters.");
    const box = renderComposer();
    audioNowMs = 7_000;
    type(box, "Too long, say");

    pressEnter(box);

    expect(box.value).toBe("Too long, say");
    expect(screen.getByRole("alert")).toHaveTextContent("at most 50,000 characters");
    audioNowMs = 8_000;
    pressEnter(box);
    expect(onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({ anchorMs: 7_000 }));
  });
});
