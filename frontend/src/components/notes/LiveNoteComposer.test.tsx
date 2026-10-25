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
    // A screen reader hears it went, from a status line that was already there.
    expect(screen.getByTestId("live-note-status")).toHaveTextContent("Note added at 01:01.");
    type(box, "N");
    expect(screen.getByTestId("live-note-status")).toHaveTextContent("Placed at 01:35, where you started typing.");
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
    expect(screen.getByTestId("live-note-status")).toHaveTextContent("at most 50,000 characters");
    expect(box).toHaveAttribute("aria-invalid", "true");
    audioNowMs = 8_000;
    pressEnter(box);
    expect(onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({ anchorMs: 7_000 }));
  });

  it("says where a note goes in a live region that is there before anything goes wrong", () => {
    renderComposer();
    const status = screen.getByTestId("live-note-status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("textbox", { name: "New note" })).toHaveAttribute(
      "aria-describedby",
      status.id,
    );
  });

  it("returns focus to the box after sending with the button", () => {
    const box = renderComposer();
    type(box, "Via the button");
    const send = screen.getByRole("button", { name: "Add note" });
    send.focus();

    fireEvent.click(send);

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(box);
  });

  it("writes times past an hour as the live lines do", () => {
    const box = renderComposer();
    audioNowMs = 3_723_000;
    type(box, "Late");
    expect(screen.getByTestId("live-note-status")).toHaveTextContent("Placed at 1:02:03, where you started typing.");
  });

  it("says the outbox's news until the next keystroke", () => {
    const { rerender } = render(
      <LiveNoteComposer readAudioNow={readAudioNow} onSubmit={onSubmit} notice={null} />,
    );
    const box = screen.getByRole("textbox", { name: "New note" }) as HTMLTextAreaElement;
    rerender(
      <LiveNoteComposer
        readAudioNow={readAudioNow}
        onSubmit={onSubmit}
        notice={{ seq: 1, text: "Note at 00:05 was deleted on another device, so it was not added again.", tone: "info" }}
      />,
    );
    const status = screen.getByTestId("live-note-status");
    expect(status).toHaveTextContent("deleted on another device");

    type(box, "Next");
    expect(status).toHaveTextContent("Placed at 00:00, where you started typing.");
  });
});

describe("LiveNoteComposer and files", () => {
  const clipboard = (text: string, files: File[]) => ({
    getData: (format: string) => (format === "text/plain" ? text : ""),
    files,
  });
  const screenshot = new File([new Uint8Array(8)], "image.png", { type: "image/png" });

  it("attaches a pasted file to the note being typed, leaving its text alone", async () => {
    const onDraftFileChange = vi.fn();
    render(
      <LiveNoteComposer
        readAudioNow={readAudioNow}
        onSubmit={onSubmit}
        onDraftFileChange={onDraftFileChange}
      />,
    );
    const box = screen.getByRole("textbox", { name: "New note" }) as HTMLTextAreaElement;
    type(box, "Slide on hiring");

    const pasted = fireEvent.paste(box, { clipboardData: clipboard("", [screenshot]) });

    // Handled, so the browser inserts nothing.
    expect(pasted).toBe(false);
    await vi.waitFor(() => expect(onDraftFileChange).toHaveBeenCalledWith(screenshot));
    expect(box.value).toBe("Slide on hiring");
  });

  it("takes a file of any type, and sends a note that holds only a file", async () => {
    const onDraftFileChange = vi.fn();
    const { rerender } = render(
      <LiveNoteComposer
        readAudioNow={readAudioNow}
        onSubmit={onSubmit}
        onDraftFileChange={onDraftFileChange}
      />,
    );
    const archive = new File([new Uint8Array(8)], "slides.zip", { type: "application/zip" });
    const box = screen.getByRole("textbox", { name: "New note" });

    fireEvent.paste(box, { clipboardData: clipboard("", [archive]) });
    await vi.waitFor(() => expect(onDraftFileChange).toHaveBeenCalledWith(archive));
    rerender(
      <LiveNoteComposer
        readAudioNow={readAudioNow}
        onSubmit={onSubmit}
        draftFile={screenshot}
        onDraftFileChange={onDraftFileChange}
      />,
    );
    expect(screen.getByTestId("note-draft-file")).toHaveTextContent("image.png");
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ kind: "point", body: "" }));
  });

  it("pastes text as text, even with a picture of it on the clipboard", () => {
    const onDraftFileChange = vi.fn();
    render(
      <LiveNoteComposer
        readAudioNow={readAudioNow}
        onSubmit={onSubmit}
        onDraftFileChange={onDraftFileChange}
      />,
    );
    const box = screen.getByRole("textbox", { name: "New note" });

    const pasted = fireEvent.paste(box, { clipboardData: clipboard("Quarterly plan", [screenshot]) });

    expect(onDraftFileChange).not.toHaveBeenCalled();
    expect(pasted).toBe(true);
  });

  it("offers Capture only where the screen is shared, naming its keys", () => {
    const onCapture = vi.fn();
    const { rerender } = render(<LiveNoteComposer readAudioNow={readAudioNow} onSubmit={onSubmit} />);
    expect(screen.queryByRole("button", { name: "Capture the shared screen" })).toBeNull();

    rerender(
      <LiveNoteComposer
        readAudioNow={readAudioNow}
        onSubmit={onSubmit}
        onCapture={onCapture}
        captureShortcut="Alt+Shift+C"
      />,
    );
    const button = screen.getByRole("button", { name: "Capture the shared screen" });
    expect(button).toHaveAttribute("title", "Capture the shared screen (Alt+Shift+C)");
    fireEvent.click(button);

    expect(onCapture).toHaveBeenCalledTimes(1);
  });

  it("says a capture's news in the status line", () => {
    const { rerender } = render(<LiveNoteComposer readAudioNow={readAudioNow} onSubmit={onSubmit} />);

    rerender(
      <LiveNoteComposer
        readAudioNow={readAudioNow}
        onSubmit={onSubmit}
        captureMessage={{ text: "Screen captured at 01:05.", tone: "info", seq: 1 }}
      />,
    );

    expect(screen.getByTestId("live-note-status")).toHaveTextContent("Screen captured at 01:05.");
  });
});
