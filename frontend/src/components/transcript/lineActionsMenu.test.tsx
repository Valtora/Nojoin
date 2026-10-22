import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";
import type { InlineNote } from "@/lib/notes/noteOutbox";
import type { TranscriptSegment } from "@/types";

import { LONG_PRESS_MS } from "./TranscriptActionsMenu";

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
afterEach(() => {
  vi.useRealTimers();
});

const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_0", start: 0, end: 2, text: "alpha and beta" },
  { id: "u1", speaker: "SPEAKER_1", start: 65, end: 67.5, text: "  forty thousand  " },
];

function renderView(overrides: Partial<ComponentProps<typeof TranscriptView>> = {}) {
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

const trigger = (name = "Actions for Blair's line at 01:05") =>
  screen.getByRole("button", { name });
const menu = () => screen.getByRole("menu");
const items = () => within(menu()).getAllByRole("menuitem");
const openMenu = (name?: string) => {
  fireEvent.click(trigger(name));
  return menu();
};
const choose = (label: string) => fireEvent.click(within(menu()).getByRole("menuitem", { name: label }));

describe("a finished line's ⋯ menu", () => {
  it("is an ARIA menu button that opens on its first item", () => {
    renderView();
    const button = trigger();
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    expect(button).toHaveAttribute("aria-expanded", "false");

    openMenu();

    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(button).toHaveAttribute("aria-controls", menu().id);
    expect(items().map((item) => item.textContent)).toEqual([
      "Note on this line",
      "Edit text",
      "Change speaker",
      "Play from here",
    ]);
    expect(items()[0]).toHaveFocus();
  });

  it("moves between items with the arrows, Home and End, wrapping at the ends", () => {
    renderView();
    openMenu();
    const [note, edit, speaker, play] = items();

    fireEvent.keyDown(note, { key: "ArrowDown" });
    expect(edit).toHaveFocus();
    fireEvent.keyDown(edit, { key: "End" });
    expect(play).toHaveFocus();
    fireEvent.keyDown(play, { key: "ArrowDown" });
    expect(note).toHaveFocus();
    fireEvent.keyDown(note, { key: "ArrowUp" });
    expect(play).toHaveFocus();
    fireEvent.keyDown(play, { key: "ArrowUp" });
    expect(speaker).toHaveFocus();
    fireEvent.keyDown(speaker, { key: "Home" });
    expect(note).toHaveFocus();
  });

  it("opens from the keyboard with ArrowDown on its button", () => {
    renderView();
    fireEvent.keyDown(trigger(), { key: "ArrowDown" });
    expect(items()[0]).toHaveFocus();
  });

  it("closes on Escape with focus back on its button, and on Tab or a press outside", () => {
    renderView();
    openMenu();
    fireEvent.keyDown(items()[1], { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger()).toHaveFocus();

    openMenu();
    fireEvent.keyDown(items()[0], { key: "Tab" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    openMenu();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("plays on from the line's start, not to the end of its group", () => {
    const props = renderView();
    openMenu();
    choose("Play from here");

    expect(props.onPlaySegment).toHaveBeenCalledWith(65);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger()).toHaveFocus();
  });

  it("opens the line's editor, and gives focus back to the ⋯ button when it closes", () => {
    renderView();
    openMenu();
    choose("Edit text");

    const editor = screen.getByDisplayValue("forty thousand", { exact: false });
    expect(editor).toHaveFocus();
    fireEvent.keyDown(editor, { key: "Escape" });
    expect(screen.queryByDisplayValue("forty thousand", { exact: false })).not.toBeInTheDocument();
    expect(trigger()).toHaveFocus();
  });

  it("opens the speaker popover against the ⋯ button, even when it is already open", () => {
    renderView();
    fireEvent.click(screen.getByRole("button", { name: "Blair" }));
    expect(screen.getByPlaceholderText("Search or add...")).toBeInTheDocument();

    const button = trigger();
    const measured = vi.spyOn(button, "getBoundingClientRect");
    openMenu();
    choose("Change speaker");

    expect(screen.getByPlaceholderText("Search or add...")).toBeInTheDocument();
    expect(measured).toHaveBeenCalled();
  });

  it("offers a read-only line only a note and playback", () => {
    renderView({
      segments: [{ ...SEGMENTS[1], provisional: true }],
    });
    openMenu();
    expect(items().map((item) => item.textContent)).toEqual(["Note on this line", "Play from here"]);
  });

  it("offers no note without a way to add one, and no playback where it is off", () => {
    renderView({ onAddNote: undefined, disableSegmentPlayback: true });
    openMenu();
    expect(items().map((item) => item.textContent)).toEqual(["Edit text", "Change speaker"]);
  });
});

describe("a note on a finished line", () => {
  it("quotes the line, sends a selection note over its span, and gives focus back", () => {
    const props = renderView();
    openMenu();
    choose("Note on this line");

    const popover = screen.getByRole("dialog", { name: "Note on the line at 01:05" });
    expect(popover).toHaveTextContent("forty thousand");
    const box = within(popover).getByRole("textbox", { name: "Note" });
    expect(box).toHaveFocus();
    fireEvent.change(box, { target: { value: "Check the figure" } });
    fireEvent.keyDown(box, { key: "Enter" });

    expect(props.onAddNote).toHaveBeenCalledWith({
      kind: "selection",
      body: "Check the figure",
      anchorMs: null,
      selection: { startMs: 65_000, endMs: 67_500, quotedText: "forty thousand" },
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger()).toHaveFocus();
  });

  it("stays open with the reason when the note is refused, and keeps what was typed", () => {
    renderView({ onAddNote: vi.fn(() => "A note can be at most 50,000 characters.") });
    openMenu();
    choose("Note on this line");
    const box = screen.getByRole("textbox", { name: "Note" });
    fireEvent.change(box, { target: { value: "Too long" } });
    fireEvent.click(screen.getByRole("button", { name: "Save note" }));

    expect(screen.getByText("A note can be at most 50,000 characters.")).toBeInTheDocument();
    expect(box).toHaveValue("Too long");
    expect(box).toHaveAttribute("aria-invalid", "true");
  });

  it("closes on a press outside only while the box is empty", () => {
    renderView();
    openMenu();
    choose("Note on this line");
    fireEvent.change(screen.getByRole("textbox", { name: "Note" }), { target: { value: "Half" } });
    fireEvent.pointerDown(document.body);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "Note" }), { target: { value: "" } });
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes on Escape with focus back on the ⋯ button, sending nothing", () => {
    const props = renderView();
    openMenu();
    choose("Note on this line");
    fireEvent.change(screen.getByRole("textbox", { name: "Note" }), { target: { value: "Never mind" } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Note" }), { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(props.onAddNote).not.toHaveBeenCalled();
    expect(trigger()).toHaveFocus();
  });

  it("shows a note still on its way on its line, with its quote, and Retry and Discard once it fails", () => {
    const failed: InlineNote = {
      key: "k1",
      id: "n1",
      kind: "selection",
      body: "Check the figure",
      start_ms: 65_000,
      end_ms: 67_500,
      quoted_text: "forty thousand",
      created_at: "2026-10-22T10:00:00Z",
      status: "failed",
      error: "Not saved: no answer from Nojoin.",
    };
    const onRetryNote = vi.fn();
    const onDiscardNote = vi.fn();
    renderView({ timelineNotes: [failed], onRetryNote, onDiscardNote });

    const note = screen.getByTestId("timeline-note");
    expect(within(note).getByTestId("timeline-note-quote")).toHaveTextContent("“forty thousand”");
    expect(note).toHaveTextContent("Not saved: no answer from Nojoin.");
    fireEvent.click(within(note).getByRole("button", { name: /^Retry note/ }));
    fireEvent.click(within(note).getByRole("button", { name: /^Discard note/ }));
    expect(onRetryNote).toHaveBeenCalledWith("k1");
    expect(onDiscardNote).toHaveBeenCalledWith("k1");
  });
});

describe("a long press on a line", () => {
  const press = (target: Element, init: PointerEventInit = {}) =>
    fireEvent.pointerDown(target, { pointerType: "touch", isPrimary: true, clientX: 10, clientY: 10, ...init });

  it("opens the menu from the line's speaker, and swallows the click its release sends", () => {
    vi.useFakeTimers();
    renderView();
    const speaker = screen.getByRole("button", { name: "Blair" });

    press(speaker);
    act(() => {
      vi.advanceTimersByTime(LONG_PRESS_MS);
    });
    fireEvent.pointerUp(speaker);
    fireEvent.click(speaker);

    expect(menu()).toBeInTheDocument();
    // The click didn't also open the speaker popover.
    expect(screen.queryByPlaceholderText("Search or add...")).not.toBeInTheDocument();
  });

  it("leaves a press on the text to the browser, which selects words", () => {
    vi.useFakeTimers();
    renderView();

    press(screen.getByText("forty thousand", { exact: false }));
    act(() => {
      vi.advanceTimersByTime(LONG_PRESS_MS * 2);
    });

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("does nothing for a touch that moves away (a scroll), or a mouse held down", () => {
    vi.useFakeTimers();
    renderView();
    const speaker = screen.getByRole("button", { name: "Blair" });

    press(speaker);
    fireEvent.pointerMove(speaker, { pointerType: "touch", clientX: 10, clientY: 40 });
    act(() => {
      vi.advanceTimersByTime(LONG_PRESS_MS * 2);
    });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    press(speaker, { pointerType: "mouse" });
    act(() => {
      vi.advanceTimersByTime(LONG_PRESS_MS * 2);
    });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
