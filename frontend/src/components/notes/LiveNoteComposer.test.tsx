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

/** Chooses a whole-meeting place from the ⋯ beside the box. */
const chooseWholeMeeting = (place: "Before the transcript" | "After the transcript") => {
  fireEvent.click(screen.getByRole("button", { name: "Note on the whole meeting" }));
  fireEvent.click(screen.getByRole("menuitem", { name: place }));
};

describe("LiveNoteComposer", () => {
  beforeEach(() => {
    audioNowMs = 0;
    readAudioNow.mockClear();
    onSubmit.mockReset();
    onSubmit.mockReturnValue(null);
  });

  it("puts a finished recording's notes before the transcript, and keeps After once chosen", () => {
    render(<LiveNoteComposer readAudioNow={readAudioNow} onSubmit={onSubmit} captureActive={false} />);
    const box = screen.getByRole("textbox", { name: "New note" }) as HTMLTextAreaElement;
    expect(screen.queryByRole("combobox")).toBeNull();
    // Before is where a finished recording's notes go, so nothing says so.
    expect(screen.queryByTestId("note-placement-chip")).toBeNull();
    type(box, "Agenda");
    pressEnter(box);
    expect(onSubmit).toHaveBeenLastCalledWith({ kind: "head", body: "Agenda", anchorMs: null });

    chooseWholeMeeting("After the transcript");
    expect(screen.getByTestId("note-placement-chip")).toHaveTextContent("After the transcript");
    expect(box).toHaveFocus();
    type(box, "Follow up");
    pressEnter(box);
    type(box, "Another follow up");
    pressEnter(box);
    expect(onSubmit).toHaveBeenLastCalledWith({ kind: "tail", body: "Another follow up", anchorMs: null });

    fireEvent.click(screen.getByRole("button", { name: "Don't put the note after the transcript" }));
    expect(screen.queryByTestId("note-placement-chip")).toBeNull();
    type(box, "Context");
    pressEnter(box);
    expect(onSubmit).toHaveBeenLastCalledWith({ kind: "head", body: "Context", anchorMs: null });
  });

  it.each([true, false])("says nothing under the box until there is news (capturing: %s)", (captureActive) => {
    render(<LiveNoteComposer readAudioNow={readAudioNow} onSubmit={onSubmit} captureActive={captureActive} />);
    const composer = screen.getByTestId("live-note-composer");
    expect(composer).not.toHaveTextContent(/labelled as yours|meeting notes|Also listed in My notes/);
    expect(composer).not.toHaveTextContent(/Placed|typing start|At send/);
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByTestId("live-note-status")).toHaveTextContent(/^$/);
    // The Shift+Enter hint is the box's description, not a line on screen.
    expect(screen.getByText("Shift+Enter starts a new line.")).toHaveClass("sr-only");
    expect(screen.getByRole("button", { name: "Add note" }))
      .toHaveAttribute("title", "Add note (Enter). Shift+Enter starts a new line.");
  });

  it("puts the next note during capture on the whole meeting, then goes back to where typing starts", () => {
    const box = renderComposer();
    expect(screen.queryByTestId("note-placement-chip")).toBeNull();

    chooseWholeMeeting("Before the transcript");
    expect(screen.getByTestId("note-placement-chip")).toHaveTextContent("Before the transcript");
    expect(box).toHaveFocus();
    type(box, "Agenda");
    pressEnter(box);
    expect(onSubmit).toHaveBeenLastCalledWith({ kind: "head", body: "Agenda", anchorMs: null });
    expect(screen.getByTestId("live-note-status")).toHaveTextContent("Note added before the transcript, and in My notes.");
    expect(screen.queryByTestId("note-placement-chip")).toBeNull();

    audioNowMs = 4_000;
    type(box, "Point");
    pressEnter(box);
    expect(onSubmit).toHaveBeenLastCalledWith({ kind: "point", body: "Point", anchorMs: 4_000 });

    chooseWholeMeeting("After the transcript");
    type(box, "Follow up Friday");
    pressEnter(box);
    expect(onSubmit).toHaveBeenLastCalledWith({ kind: "tail", body: "Follow up Friday", anchorMs: null });
  });

  it("takes a whole-meeting place off with the chip's button, back to where typing started", () => {
    const box = renderComposer();
    chooseWholeMeeting("After the transcript");
    audioNowMs = 5_000;
    type(box, "On second thoughts");
    audioNowMs = 9_000;
    fireEvent.click(screen.getByRole("button", { name: "Don't put the note after the transcript" }));

    expect(screen.queryByTestId("note-placement-chip")).toBeNull();
    expect(box).toHaveFocus();
    pressEnter(box);
    expect(onSubmit).toHaveBeenLastCalledWith({ kind: "point", body: "On second thoughts", anchorMs: 5_000 });
  });

  it("shows a time a line's menu set during capture, and takes it off for where typing starts", () => {
    const onDraftChange = vi.fn();
    render(<LiveNoteComposer readAudioNow={readAudioNow} onSubmit={onSubmit} onDraftChange={onDraftChange}
      draft={{ text: "", mode: "typing", typingStartMs: 60_000, typingStartFromLine: true }} />);
    expect(screen.getByTestId("note-placement-chip")).toHaveTextContent("At 01:00");
    expect(screen.getByRole("textbox", { name: "New note" }).getAttribute("aria-describedby"))
      .toContain(screen.getByTestId("note-placement-chip").id);

    fireEvent.click(screen.getByRole("button", { name: "Don't put the note at 01:00" }));
    expect(onDraftChange).toHaveBeenLastCalledWith(
      { text: "", mode: "typing", typingStartMs: null, typingStartFromLine: undefined },
    );
  });

  it("places a draft carried through Stop where typing started, showing that time", () => {
    const draft = { text: "Carried thought", mode: "typing" as const, typingStartMs: 12_000 };
    const onDraftChange = vi.fn();
    render(<LiveNoteComposer readAudioNow={readAudioNow} onSubmit={onSubmit} captureActive={false}
      draft={draft} onDraftChange={onDraftChange} />);
    expect(screen.getByTestId("note-placement-chip")).toHaveTextContent("At 00:12");
    expect(onDraftChange).not.toHaveBeenCalled();
    audioNowMs = 63_000;
    pressEnter(screen.getByRole("textbox", { name: "New note" }) as HTMLTextAreaElement);
    expect(onSubmit).toHaveBeenLastCalledWith({ kind: "point", body: "Carried thought", anchorMs: 12_000 });
  });

  it("puts a carried draft before the transcript instead when its time is taken off", () => {
    const onDraftChange = vi.fn();
    render(<LiveNoteComposer readAudioNow={readAudioNow} onSubmit={onSubmit} captureActive={false}
      draft={{ text: "Carried thought", mode: "typing", typingStartMs: 12_000 }} onDraftChange={onDraftChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Don't put the note at 00:12" }));
    expect(onDraftChange).toHaveBeenLastCalledWith(
      { text: "Carried thought", mode: "head", typingStartMs: null, typingStartFromLine: undefined },
    );
  });

  it("shows the retained time for a carried file without caption after Stop", () => {
    const file = new File(["picture"], "context.png", { type: "image/png" });
    render(<LiveNoteComposer readAudioNow={readAudioNow} onSubmit={onSubmit} captureActive={false}
      draft={{ text: "", mode: "typing", typingStartMs: 12_000 }} draftFile={file} onDraftFileChange={vi.fn()} />);
    expect(screen.getByTestId("note-draft-file")).toHaveTextContent("context.png");
    expect(screen.getByTestId("note-placement-chip")).toHaveTextContent("At 00:12");
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    expect(onSubmit).toHaveBeenLastCalledWith({ kind: "point", body: "", anchorMs: 12_000 });
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
    expect(screen.getByTestId("live-note-status")).toHaveTextContent(/^$/);
    // Typing start is where a note goes during capture, so nothing says so.
    expect(screen.queryByTestId("note-placement-chip")).toBeNull();
  });

  it("drops the anchor when the box is emptied, and takes a new one on the next keystroke", () => {
    const box = renderComposer();

    audioNowMs = 10_000;
    type(box, "Wrong");
    type(box, "");
    audioNowMs = 40_000;
    type(box, "  ");
    expect(readAudioNow).toHaveBeenCalledTimes(1);
    audioNowMs = 42_000;
    type(box, "  Right");
    pressEnter(box);

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ anchorMs: 42_000 }));
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

  it("says what happened in a live region that is there before anything goes wrong", () => {
    renderComposer();
    const status = screen.getByTestId("live-note-status");
    expect(status).toHaveAttribute("aria-live", "polite");
    const box = screen.getByRole("textbox", { name: "New note" });
    expect(box.getAttribute("aria-describedby")?.split(" ")).toContain(status.id);
    expect(box).toHaveAccessibleDescription(/Shift\+Enter starts a new line\./);
  });

  it("returns focus to the box after sending with the button", () => {
    const box = renderComposer();
    const composer = screen.getByRole("form", { name: "Add a note" });
    expect(composer.tagName).toBe("FORM");
    expect(composer).toHaveAttribute("data-ui", "surface");
    expect(composer).toHaveAttribute("data-ui-surface", "pane");
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
    pressEnter(box);
    expect(screen.getByTestId("live-note-status")).toHaveTextContent("Note added at 1:02:03.");
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
    expect(status).toHaveTextContent(/^$/);
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
