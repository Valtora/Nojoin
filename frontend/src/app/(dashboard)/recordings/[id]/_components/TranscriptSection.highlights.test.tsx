import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { forgetNoteOutboxes, noteOutboxFor } from "@/lib/notes/noteOutboxes";
import { interpolatedTokens } from "@/lib/notes/selectionRange";
import {
  RecordingStatus,
  type Recording,
  type TimelineNote,
  type TranscriptSegment,
} from "@/types";

import TranscriptSection from "./TranscriptSection";

const createTimelineNote = vi.fn();
const updateTimelineNote = vi.fn();
const deleteTimelineNote = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  createTimelineNote: (...args: unknown[]) => createTimelineNote(...args),
  updateTimelineNote: (...args: unknown[]) => updateTimelineNote(...args),
  deleteTimelineNote: (...args: unknown[]) => deleteTimelineNote(...args),
}));
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

const VENUE: TranscriptSegment = {
  id: "u1",
  speaker: "SPEAKER_1",
  start: 65,
  end: 68.6,
  text: "We need forty thousand for the venue",
};
const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_0", start: 0, end: 2, text: "Good morning" },
  VENUE,
];

/** The span a selection of `words` in the venue line had (B3c), with `quote` as written. */
const spanOf = (words: string) => {
  const from = VENUE.text.indexOf(words);
  const tokens = interpolatedTokens(VENUE.text, VENUE.start * 1000, VENUE.end * 1000).filter(
    (token) => token.from < from + words.length && token.to > from,
  );
  return { start_ms: tokens[0].startMs, end_ms: tokens[tokens.length - 1].endMs };
};

const selectionNote = (id: string, words: string, quote: string, body: string): TimelineNote => ({
  id,
  recording_id: "rec-1",
  kind: "selection",
  body,
  ...spanOf(words),
  quoted_text: quote,
  revision: 3,
  created_at: "2026-10-23T10:00:00",
  updated_at: "2026-10-23T10:00:00",
});

/** Written on the words as they read now. */
const EXACT = selectionNote("n-exact", "forty thousand", "forty thousand", "Check the figure");
/** Written on the live text, which heard "fourteen"; the final pass heard "forty". */
const FUZZY = selectionNote("n-fuzzy", "forty thousand for the venue", "fourteen thousand for the venue", "Is that per day?");
/** Written on words the final pass no longer has anywhere near. */
const GONE = selectionNote("n-gone", "forty thousand", "a budget of twelve", "Ask finance");

const RECORDING = {
  id: "rec-1",
  name: "Weekly sync",
  status: RecordingStatus.PROCESSED,
  duration_seconds: 600,
  speakers: [],
} as unknown as Recording;

const renderSection = (timelineNotes: TimelineNote[]) =>
  render(
    <TranscriptSection
      active
      recording={RECORDING}
      timelineNotes={timelineNotes}
      transcriptSegments={SEGMENTS}
      currentTime={-1}
      isPlaying={false}
      speakerMap={{ SPEAKER_0: "Alex", SPEAKER_1: "Blair" }}
      speakerColors={{}}
      globalSpeakers={[]}
      canUndo={false}
      canRedo={false}
      deferredTranscriptUtteranceIds={[]}
      onPlaySegment={vi.fn()}
      onPause={vi.fn()}
      onResume={vi.fn()}
      onRenameSpeaker={vi.fn()}
      onUpdateSegmentSpeaker={vi.fn()}
      onUpdateSegmentText={vi.fn()}
      onFindAndReplace={vi.fn()}
      onUndo={vi.fn()}
      onRedo={vi.fn()}
      onExport={vi.fn()}
      onActiveEditUtteranceChange={vi.fn()}
    />,
  );

const venueText = () => screen.getByText((_, element) => element?.tagName === "P" && element.textContent === VENUE.text);
const marks = () => Array.from(venueText().querySelectorAll<HTMLElement>("mark[data-note-id]"));
const noteLine = (id: string) =>
  screen.getAllByTestId("timeline-note").find((line) => line.dataset.noteId === id)!;

const conflict = (note: TimelineNote) => ({
  isAxiosError: true,
  response: {
    status: 409,
    data: { detail: { code: "revision_conflict", message: "changed", note } },
  },
});

beforeEach(() => {
  forgetNoteOutboxes();
  createTimelineNote.mockReset();
  updateTimelineNote.mockReset();
  deleteTimelineNote.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the words a note is on, in the processed transcript", () => {
  it("are tinted and underlined where the quote is found as written, the line's text unchanged", () => {
    renderSection([EXACT]);

    expect(marks()).toHaveLength(1);
    expect(marks()[0]).toHaveTextContent(/^forty thousand$/);
    expect(marks()[0].dataset).toMatchObject({ noteId: "n-exact", noteAnchor: "exact" });
    expect(marks()[0].className).toMatch(/decoration-solid/);
    expect(marks()[0].className).not.toMatch(/decoration-dotted/);
    expect(marks()[0]).toHaveAttribute("title", "Note: “Check the figure”");
    expect(venueText().textContent).toBe(VENUE.text);
    expect(within(noteLine("n-exact")).queryByTestId("timeline-note-words-changed")).toBeNull();
  });

  it("are underlined with dots where found with changes, the quote as written in the title", () => {
    renderSection([FUZZY]);

    expect(marks()).toHaveLength(1);
    expect(marks()[0]).toHaveTextContent(/^forty thousand for the venue$/);
    expect(marks()[0].dataset.noteAnchor).toBe("fuzzy");
    expect(marks()[0].className).toMatch(/decoration-dotted/);
    expect(marks()[0].title).toContain("Written on “fourteen thousand for the venue”");
    expect(within(noteLine("n-fuzzy")).queryByTestId("timeline-note-words-changed")).toBeNull();
  });

  it("are marked once the note is saved, not before, so no mark opens an editor that can't save", async () => {
    createTimelineNote.mockRejectedValueOnce({ isAxiosError: true, message: "Network Error" });
    noteOutboxFor("rec-1").submit({
      kind: "selection",
      body: "Check the figure",
      anchorMs: null,
      selection: { startMs: EXACT.start_ms!, endMs: EXACT.end_ms!, quotedText: "forty thousand" },
    });
    renderSection([]);

    const line = screen.getByTestId("timeline-note");
    await waitFor(() => expect(line).toHaveAttribute("data-note-status", "failed"));
    expect(marks()).toHaveLength(0);
    expect(within(line).queryByRole("button", { name: /^Edit note/ })).toBeNull();

    createTimelineNote.mockImplementationOnce(async (_recording: string, payload: { id: string }) => ({
      ...EXACT,
      id: payload.id,
      revision: 1,
    }));
    fireEvent.click(within(line).getByRole("button", { name: /^Retry note/ }));
    await waitFor(() => expect(marks()).toHaveLength(1));
    expect(marks()[0]).toHaveTextContent(/^forty thousand$/);
    expect(
      within(screen.getByTestId("timeline-note")).getByRole("button", { name: /^Edit note/ }),
    ).toBeInTheDocument();
  });

  it("are not marked when gone; the note line strikes the quote and says what is there now", () => {
    renderSection([GONE]);

    expect(marks()).toHaveLength(0);
    const line = noteLine("n-gone");
    expect(within(line).getByTestId("timeline-note-quote").className).toMatch(/line-through/);
    expect(within(line).getByTestId("timeline-note-words-changed")).toHaveTextContent(
      "Words changed since this note. Here it now reads “forty thousand”.",
    );
  });
});

describe("editing a note in the processed transcript", () => {
  const openFromLine = (id: string, body: string) => {
    fireEvent.click(within(noteLine(id)).getByRole("button", { name: `Edit note at 01:05, “${body}”` }));
    return screen.getByRole("dialog", { name: "Edit the note at 01:05" });
  };

  it("saves new text against the note's revision, shows it, and gives focus back", async () => {
    updateTimelineNote.mockImplementation(async (_recording, _id, update) => ({
      ...EXACT,
      body: update.body,
      revision: 4,
    }));
    renderSection([EXACT]);

    const editor = openFromLine("n-exact", "Check the figure");
    const box = within(editor).getByRole("textbox", { name: "Note" });
    expect(box).toHaveValue("Check the figure");
    fireEvent.change(box, { target: { value: "  Check it with finance  " } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save note" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(updateTimelineNote).toHaveBeenCalledWith("rec-1", "n-exact", {
      expected_revision: 3,
      body: "Check it with finance",
    });
    expect(noteLine("n-exact")).toHaveTextContent("Check it with finance");
    expect(document.activeElement).toBe(
      within(noteLine("n-exact")).getByRole("button", { name: /^Edit note at 01:05/ }),
    );
    // Opened again, it edits the revision the save made.
    fireEvent.change(within(openFromLine("n-exact", "Check it with finance")).getByRole("textbox"), {
      target: { value: "Again" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save note" }));
    await waitFor(() => expect(updateTimelineNote).toHaveBeenCalledTimes(2));
    expect(updateTimelineNote.mock.calls[1][2]).toEqual({ expected_revision: 4, body: "Again" });
  });

  it("opens from a click on the marked words, but not from a drag that selected text", () => {
    renderSection([EXACT]);

    const selection = { isCollapsed: false } as Selection;
    const getSelection = vi.spyOn(window, "getSelection").mockReturnValue(selection);
    fireEvent.click(marks()[0]);
    expect(screen.queryByRole("dialog")).toBeNull();

    getSelection.mockReturnValue({ isCollapsed: true } as Selection);
    fireEvent.click(marks()[0]);
    expect(
      within(screen.getByRole("dialog", { name: "Edit the note at 01:05" })).getByRole("textbox"),
    ).toHaveValue("Check the figure");
  });

  it("keeps an editor holding typed text when another note's words are pressed", () => {
    // On the line before, so neither note folds into a count.
    const morning: TimelineNote = {
      ...EXACT,
      id: "n-morning",
      body: "Say hello",
      start_ms: 0,
      end_ms: 2_000,
      quoted_text: "Good morning",
    };
    renderSection([EXACT, morning]);
    const other = () => document.querySelector<HTMLElement>('mark[data-note-id="n-morning"]')!;
    const press = (element: HTMLElement) => {
      fireEvent.pointerDown(element);
      fireEvent.click(element);
    };

    const editor = openFromLine("n-exact", "Check the figure");
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Mine" } });
    press(other());
    expect(within(screen.getByRole("dialog", { name: "Edit the note at 01:05" })).getByRole("textbox")).toHaveValue(
      "Mine",
    );

    // Back as it opened, it gives way to the words pressed.
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Check the figure" } });
    press(other());
    expect(within(screen.getByRole("dialog")).getByRole("textbox")).toHaveValue("Say hello");
  });

  it("on a change made elsewhere, shows the note as it is now and keeps the text typed", async () => {
    updateTimelineNote.mockRejectedValueOnce(conflict({ ...EXACT, body: "Changed on my phone", revision: 5 }));
    renderSection([EXACT]);

    const editor = openFromLine("n-exact", "Check the figure");
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Mine" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save note" }));

    await within(editor).findByText(
      "This note was changed elsewhere. It now reads “Changed on my phone”. Save again to keep yours.",
    );
    expect(within(editor).getByRole("textbox")).toHaveValue("Mine");
    expect(noteLine("n-exact")).toHaveTextContent("Changed on my phone");

    updateTimelineNote.mockResolvedValueOnce({ ...EXACT, body: "Mine", revision: 6 });
    fireEvent.click(within(editor).getByRole("button", { name: "Save note" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(updateTimelineNote.mock.calls[1][2]).toEqual({ expected_revision: 5, body: "Mine" });
    expect(noteLine("n-exact")).toHaveTextContent("Mine");
  });

  it("counts a save sent again after a lost answer as saved, when the note already reads so", async () => {
    updateTimelineNote.mockRejectedValueOnce(conflict({ ...EXACT, body: "Mine", revision: 4 }));
    renderSection([EXACT]);

    const editor = openFromLine("n-exact", "Check the figure");
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Mine" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save note" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(noteLine("n-exact")).toHaveTextContent("Mine");
  });

  it("deletes on a second press, against its revision: its line and marks go, focus to the transcript", async () => {
    deleteTimelineNote.mockResolvedValue(undefined);
    renderSection([EXACT]);

    const editor = openFromLine("n-exact", "Check the figure");
    fireEvent.click(within(editor).getByRole("button", { name: "Delete" }));
    expect(deleteTimelineNote).not.toHaveBeenCalled();
    fireEvent.click(within(editor).getByRole("button", { name: "Confirm delete" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(deleteTimelineNote).toHaveBeenCalledWith("rec-1", "n-exact", { expectedRevision: 3 });
    expect(screen.queryByTestId("timeline-note")).toBeNull();
    expect(marks()).toHaveLength(0);
    expect(document.activeElement).toHaveAttribute("id", "panel-transcript");
  });

  it("keeps a note changed elsewhere when deleting it, and says what it reads now", async () => {
    deleteTimelineNote.mockRejectedValueOnce(conflict({ ...EXACT, body: "Changed on my phone", revision: 5 }));
    renderSection([EXACT]);

    const editor = openFromLine("n-exact", "Check the figure");
    fireEvent.click(within(editor).getByRole("button", { name: "Delete" }));
    fireEvent.click(within(editor).getByRole("button", { name: "Confirm delete" }));

    await within(editor).findByText(
      "This note was changed elsewhere. It now reads “Changed on my phone”. Delete again to remove it anyway.",
    );
    expect(noteLine("n-exact")).toHaveTextContent("Changed on my phone");
    expect(marks()).toHaveLength(1);
  });

  it("puts focus back in the box when a save is refused, and says why as an alert", async () => {
    updateTimelineNote.mockRejectedValueOnce({ isAxiosError: true, message: "Network Error" });
    renderSection([EXACT]);

    const editor = openFromLine("n-exact", "Check the figure");
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Mine" } });
    const save = within(editor).getByRole("button", { name: "Save note" });
    save.focus();
    fireEvent.click(save);

    expect(await within(editor).findByRole("alert")).toHaveTextContent("No answer from Nojoin. Try again.");
    expect(document.activeElement).toBe(within(editor).getByRole("textbox"));
  });
});

describe("leaving the page with a note's change unsaved", () => {
  const leave = () => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  };
  const openFromLine = (id: string, body: string) => {
    fireEvent.click(within(noteLine(id)).getByRole("button", { name: `Edit note at 01:05, “${body}”` }));
    return screen.getByRole("dialog", { name: "Edit the note at 01:05" });
  };

  it("asks while an edit is on its way, and not once it is saved", async () => {
    let answer: (note: TimelineNote) => void = () => undefined;
    updateTimelineNote.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)));
    renderSection([EXACT]);

    const editor = openFromLine("n-exact", "Check the figure");
    expect(leave()).toBe(false);
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Mine" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save note" }));
    expect(leave()).toBe(true);

    answer({ ...EXACT, body: "Mine", revision: 4 });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(leave()).toBe(false);
  });

  it("asks while a refused delete is still open, and not once it is given up", async () => {
    deleteTimelineNote.mockRejectedValueOnce(conflict({ ...EXACT, body: "Changed on my phone", revision: 5 }));
    renderSection([EXACT]);

    const editor = openFromLine("n-exact", "Check the figure");
    fireEvent.click(within(editor).getByRole("button", { name: "Delete" }));
    fireEvent.click(within(editor).getByRole("button", { name: "Confirm delete" }));
    await within(editor).findByRole("alert");
    expect(leave()).toBe(true);

    fireEvent.click(within(editor).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(leave()).toBe(false);
  });
});
