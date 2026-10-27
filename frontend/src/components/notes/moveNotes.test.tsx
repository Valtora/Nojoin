import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptSection from "@/app/(dashboard)/recordings/[id]/_components/TranscriptSection";
import { createTimelineNote } from "@/lib/api";
import { forgetNoteOutboxes } from "@/lib/notes/noteOutboxes";
import { useNoteEdits } from "@/lib/notes/useNoteEdits";
import {
  RecordingStatus,
  type Recording,
  type TimelineNote,
  type TranscriptSegment,
} from "@/types";

import MyNotesList from "./MyNotesList";

const updateTimelineNote = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  createTimelineNote: vi.fn(),
  updateTimelineNote: (...args: unknown[]) => updateTimelineNote(...args),
  deleteTimelineNote: vi.fn(),
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

const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_0", start: 0, end: 2, text: "Good morning" },
  { id: "u1", speaker: "SPEAKER_1", start: 65, end: 68, text: "Forty thousand for the venue" },
  { id: "u2", speaker: "SPEAKER_0", start: 130, end: 133, text: "Let's move on" },
  { id: "u3", speaker: "SPEAKER_1", start: 200, end: 204, text: "Next item is hiring" },
];

const RECORDING = {
  id: "rec-1",
  name: "Weekly sync",
  status: RecordingStatus.PROCESSED,
  duration_seconds: 600,
  speakers: [],
} as unknown as Recording;

const note = (
  id: string,
  kind: TimelineNote["kind"],
  body: string,
  fields: Partial<TimelineNote> = {},
): TimelineNote => ({
  id,
  recording_id: "rec-1",
  kind,
  body,
  revision: 2,
  created_at: "2026-10-23T10:00:00",
  updated_at: "2026-10-23T10:00:00",
  ...fields,
});

// After line u1, which starts at 01:05.
const POINT = note("n-point", "point", "Ask about catering", { anchor_ms: 66_000 });
const SELECTION = note("n-sel", "selection", "Who owns hiring?", {
  start_ms: 201_000,
  end_ms: 202_500,
  quoted_text: "is hiring",
});

const conflict = (current: TimelineNote) => ({
  isAxiosError: true,
  response: {
    status: 409,
    data: { detail: { code: "revision_conflict", message: "changed", note: current } },
  },
});

/** The transcript and My notes sharing one set of edits, as on the recording page. */
function Page({ notes, segments = SEGMENTS }: { notes: TimelineNote[]; segments?: TranscriptSegment[] }) {
  const noteEdits = useNoteEdits(RECORDING.id);
  return (
    <>
      <MyNotesList
        recordingId={RECORDING.id}
        timelineNotes={notes}
        segments={segments}
        noteEdits={noteEdits}
        onPlay={vi.fn()}
      />
      <TranscriptSection
        active
        recording={RECORDING}
        timelineNotes={notes}
        noteEdits={noteEdits}
        transcriptSegments={segments}
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
        onRefresh={vi.fn()}
        onUpdateSegmentSpeaker={vi.fn()}
        onUpdateSegmentText={vi.fn()}
        onFindAndReplace={vi.fn()}
        onUndo={vi.fn()}
        onRedo={vi.fn()}
        onExport={vi.fn()}
        onActiveEditUtteranceChange={vi.fn()}
      />
    </>
  );
}

const scrollRegion = () => screen.getByTestId("transcript-scroll-region");
const noteLine = (id: string) =>
  screen.queryAllByTestId("timeline-note").find((line) => line.dataset.noteId === id);
const line = (index: number) =>
  scrollRegion().querySelector<HTMLElement>(`[data-order-index="${index}"]`)!;
const moveButton = (id: string, direction: "up" | "down") =>
  within(noteLine(id)!).getByRole("button", { name: new RegExp(`^Move note ${direction} `) });

/** The transcript as it reads, top to bottom: `line:i` for a line, `note:id` for a note. */
const transcriptOrder = () =>
  Array.from(
    scrollRegion().querySelectorAll<HTMLElement>('[data-order-index], [data-testid="timeline-note"]'),
  ).map((element) =>
    element.dataset.noteId ? `note:${element.dataset.noteId}` : `line:${element.dataset.orderIndex}`,
  );

const myNotesTime = (id: string) =>
  screen
    .getAllByTestId("my-note")
    .find((row) => row.dataset.noteId === id)!
    .querySelector(".font-mono")?.textContent;

/** A promise to settle by hand, for a write on its way. */
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((settle, fail) => {
    resolve = settle;
    reject = fail;
  });
  return { promise, resolve, reject };
};

const moved = (anchorMs: number, revision = 3): TimelineNote => ({ ...POINT, anchor_ms: anchorMs, revision });

beforeEach(() => {
  forgetNoteOutboxes();
  updateTimelineNote.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Moving a point note with Move up and Move down", () => {
  it("allows timeline movement onto settling destinations while checking their identity and revision", async () => {
    updateTimelineNote.mockImplementation(async (_recording, _id, update) => moved(update.anchor_ms));
    const segments = SEGMENTS.map((segment, index) => ({ ...segment, settling_required: true,
      revision: 8, provisional: true, locked: index !== 2 }));
    render(<Page notes={[POINT]} segments={segments} />);
    fireEvent.click(moveButton("n-point", "down"));
    await waitFor(() => expect(updateTimelineNote).toHaveBeenCalledWith("rec-1", "n-point", {
      expected_revision: 2, anchor_ms: 130_000, target_utterance_id: "u2", target_utterance_revision: 8,
    }));
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "line:2", "note:n-point", "line:3"]);
  });
  it("moves it after the next line, anchored at that line's start, and keeps focus on the button", async () => {
    updateTimelineNote.mockImplementation(async (_recording, _id, update) => moved(update.anchor_ms));
    render(<Page notes={[POINT]} />);
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "note:n-point", "line:2", "line:3"]);

    fireEvent.click(moveButton("n-point", "down"));

    await waitFor(() =>
      expect(updateTimelineNote).toHaveBeenCalledWith("rec-1", "n-point", {
        expected_revision: 2,
        anchor_ms: 130_000,
      }),
    );
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "line:2", "note:n-point", "line:3"]);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Note moved after the line at 02:10."));
    expect(document.activeElement).toBe(moveButton("n-point", "down"));

    // Pressing it again carries on, against the revision the first move made.
    fireEvent.click(document.activeElement as HTMLElement);
    await waitFor(() =>
      expect(updateTimelineNote).toHaveBeenLastCalledWith("rec-1", "n-point", {
        expected_revision: 3,
        anchor_ms: 200_000,
      }),
    );
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "line:2", "line:3", "note:n-point"]);
  });

  it("sends a second press after the first is answered, against the revision it made", async () => {
    const first = deferred<TimelineNote>();
    updateTimelineNote.mockReturnValueOnce(first.promise);
    render(<Page notes={[POINT]} />);

    fireEvent.click(moveButton("n-point", "down"));
    fireEvent.click(moveButton("n-point", "down"));
    // Both show at once; only the first is on its way.
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "line:2", "line:3", "note:n-point"]);
    await waitFor(() => expect(updateTimelineNote).toHaveBeenCalledTimes(1));

    updateTimelineNote.mockImplementation(async (_recording, _id, update) => moved(update.anchor_ms, 4));
    await act(async () => first.resolve(moved(130_000, 3)));

    await waitFor(() => expect(updateTimelineNote).toHaveBeenCalledTimes(2));
    expect(updateTimelineNote).toHaveBeenLastCalledWith("rec-1", "n-point", {
      expected_revision: 3,
      anchor_ms: 200_000,
    });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Note moved after the line at 03:20."));
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "line:2", "line:3", "note:n-point"]);
  });

  it("moves it up, and has no step past the first or last line", async () => {
    updateTimelineNote.mockImplementation(async (_recording, _id, update) => moved(update.anchor_ms));
    render(<Page notes={[POINT]} />);

    fireEvent.click(moveButton("n-point", "up"));

    await waitFor(() =>
      expect(updateTimelineNote).toHaveBeenCalledWith("rec-1", "n-point", {
        expected_revision: 2,
        anchor_ms: 0,
      }),
    );
    expect(transcriptOrder()).toEqual(["line:0", "note:n-point", "line:1", "line:2", "line:3"]);
    // At the top, focus goes to the step that still works.
    await waitFor(() => expect(document.activeElement).toBe(moveButton("n-point", "down")));
    expect(moveButton("n-point", "up")).toBeDisabled();
  });

  it("shows the move in My notes, with the note's new time", async () => {
    updateTimelineNote.mockImplementation(async (_recording, _id, update) => moved(update.anchor_ms));
    const early = note("n-early", "point", "Early note", { anchor_ms: 1_000 });
    render(<Page notes={[POINT, early]} />);
    const listOrder = () => screen.getAllByTestId("my-note").map((row) => row.dataset.noteId);
    expect(listOrder()).toEqual(["n-early", "n-point"]);

    fireEvent.click(moveButton("n-early", "down"));
    // Moved in beside a saved note, it stays shown rather than folding into "2 notes".
    expect(screen.queryByTestId("line-notes-chip")).toBeNull();
    fireEvent.click(moveButton("n-early", "down"));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Note moved after the line at 02:10."));
    expect(myNotesTime("n-early")).toBe("02:10");

    expect(listOrder()).toEqual(["n-point", "n-early"]);
    expect(myNotesTime("n-point")).toBe("01:06");
  });

  it("offers no move while the note's editor is open", () => {
    render(<Page notes={[POINT]} />);

    fireEvent.click(within(noteLine("n-point")!).getByRole("button", { name: /^Edit note at 01:06/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(noteLine("n-point")!).queryByRole("button", { name: /^Move note/ })).toBeNull();
    expect(noteLine("n-point")!.querySelector("[data-note-grip]")).toBeNull();

    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(moveButton("n-point", "down")).toBeInTheDocument();
  });

  it("offers no move for a selection note, whose words fix its place", () => {
    render(<Page notes={[POINT, SELECTION]} />);

    expect(within(noteLine("n-sel")!).queryByRole("button", { name: /^Move note/ })).toBeNull();
    expect(noteLine("n-sel")!.querySelector("[data-note-grip]")).toBeNull();
    expect(noteLine("n-point")!.querySelector("[data-note-grip]")).not.toBeNull();
  });
});

describe("A refused move", () => {
  it("shows the note at its new place while it saves, and puts it back on a 409 with the note as it is now", async () => {
    const answer = deferred<TimelineNote>();
    updateTimelineNote.mockReturnValueOnce(answer.promise);
    render(<Page notes={[POINT]} />);

    fireEvent.click(moveButton("n-point", "down"));
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "line:2", "note:n-point", "line:3"]);

    // Edited on another device meanwhile: still at 01:06, at revision 3.
    const current = { ...POINT, body: "Ask about catering and drinks", revision: 3 };
    await act(async () => answer.reject(conflict(current)));

    expect(transcriptOrder()).toEqual(["line:0", "line:1", "note:n-point", "line:2", "line:3"]);
    expect(noteLine("n-point")).toHaveTextContent("Ask about catering and drinks");
    expect(within(noteLine("n-point")!).getByTestId("timeline-note-move-refused")).toHaveTextContent(
      "This note was changed elsewhere. It now reads “Ask about catering and drinks”. Move it again if you still want it there.",
    );
    expect(screen.getByRole("status")).toHaveTextContent(/^Note not moved\. This note was changed elsewhere/);
    expect(document.activeElement).toBe(moveButton("n-point", "down"));

    // A second try goes against the revision the 409 carried.
    updateTimelineNote.mockImplementation(async (_recording, _id, update) => ({
      ...current,
      anchor_ms: update.anchor_ms,
      revision: 4,
    }));
    fireEvent.click(moveButton("n-point", "down"));
    await waitFor(() =>
      expect(updateTimelineNote).toHaveBeenLastCalledWith("rec-1", "n-point", {
        expected_revision: 3,
        anchor_ms: 130_000,
      }),
    );
    await waitFor(() => expect(noteLine("n-point")!.querySelector('[data-testid="timeline-note-move-refused"]')).toBeNull());
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "line:2", "note:n-point", "line:3"]);
  });

  it("drops a press queued behind a refused move, which stepped from a place the note never reached", async () => {
    const answer = deferred<TimelineNote>();
    updateTimelineNote.mockReturnValueOnce(answer.promise);
    render(<Page notes={[POINT]} />);

    fireEvent.click(moveButton("n-point", "down"));
    fireEvent.click(moveButton("n-point", "down"));
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "line:2", "line:3", "note:n-point"]);

    updateTimelineNote.mockImplementation(async (_recording, _id, update) => moved(update.anchor_ms, 4));
    const current = { ...POINT, body: "Ask about catering and drinks", revision: 3 };
    await act(async () => answer.reject(conflict(current)));

    expect(updateTimelineNote).toHaveBeenCalledTimes(1);
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "note:n-point", "line:2", "line:3"]);
    expect(within(noteLine("n-point")!).getByTestId("timeline-note-move-refused")).toHaveTextContent(
      /^This note was changed elsewhere/,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/^Note not moved\./);

    // A press made once the refusal shows goes ahead.
    fireEvent.click(moveButton("n-point", "down"));
    await waitFor(() =>
      expect(updateTimelineNote).toHaveBeenLastCalledWith("rec-1", "n-point", {
        expected_revision: 3,
        anchor_ms: 130_000,
      }),
    );
  });

  it("follows another device's move that the 409 carries", async () => {
    updateTimelineNote.mockRejectedValueOnce(conflict({ ...POINT, anchor_ms: 200_500, revision: 3 }));
    render(<Page notes={[POINT]} />);

    fireEvent.click(moveButton("n-point", "up"));

    await waitFor(() =>
      expect(transcriptOrder()).toEqual(["line:0", "line:1", "line:2", "line:3", "note:n-point"]),
    );
    expect(myNotesTime("n-point")).toBe("03:20");
  });

  it("puts the note back when Nojoin doesn't answer", async () => {
    updateTimelineNote.mockRejectedValueOnce(new Error("Network Error"));
    render(<Page notes={[POINT]} />);

    fireEvent.click(moveButton("n-point", "down"));

    await waitFor(() =>
      expect(within(noteLine("n-point")!).getByTestId("timeline-note-move-refused")).toHaveTextContent(
        "No answer from Nojoin. Try again.",
      ),
    );
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "note:n-point", "line:2", "line:3"]);
    expect(myNotesTime("n-point")).toBe("01:06");
  });
});

describe("Dragging a point note by its grip", () => {
  const originalElementFromPoint = document.elementFromPoint;
  let under: Element | null = null;
  beforeEach(() => {
    under = null;
    document.elementFromPoint = () => under;
  });
  afterEach(() => {
    document.elementFromPoint = originalElementFromPoint;
  });

  const grip = (id: string) => noteLine(id)!.querySelector<HTMLElement>("[data-note-grip]")!;
  const dragOver = (element: Element | null) => {
    under = element;
    fireEvent.pointerMove(window, { clientX: 20, clientY: 40, buttons: 1 });
  };

  it("drags a newly saved marked line note without a quote onto an open line", async () => {
    const segments = SEGMENTS.map((segment) => ({ ...segment, settling_required: true,
      revision: 8, provisional: true, locked: false }));
    let created: TimelineNote | undefined;
    vi.mocked(createTimelineNote).mockImplementation(async (_recording, payload) => {
      created = note(payload.id!, payload.kind, payload.body, { anchor_ms: payload.anchor_ms,
        start_ms: payload.start_ms, end_ms: payload.end_ms, quoted_text: null });
      return created;
    });
    updateTimelineNote.mockImplementation(async (_recording, _id, update) => ({ ...created!,
      anchor_ms: update.anchor_ms, start_ms: update.anchor_ms, end_ms: update.anchor_ms + 3000, revision: 3 }));
    render(<Page notes={[]} segments={segments} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for Blair's line at 01:05" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Line thought" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save note" }));
    await waitFor(() => expect(created).toBeDefined());
    await waitFor(() => expect(grip(created!.id)).toBeInTheDocument());
    expect(created).toMatchObject({ kind: "point", anchor_ms: 65_000, start_ms: 65_000,
      end_ms: 68_000, quoted_text: null });
    expect(within(noteLine(created!.id)!).queryByTestId("timeline-note-quote")).toBeNull();
    fireEvent.pointerDown(grip(created!.id), { pointerType: "mouse", button: 0 });
    dragOver(line(2));
    fireEvent.pointerUp(window);
    await waitFor(() => expect(updateTimelineNote).toHaveBeenCalledWith("rec-1", created!.id, {
      expected_revision: 2, anchor_ms: 130_000, target_utterance_id: "u2", target_utterance_revision: 8,
    }));
    expect(myNotesTime(created!.id)).toBe("02:10");
  });

  it("drops it after the line it is let go over, anchored at that line's start", async () => {
    updateTimelineNote.mockImplementation(async (_recording, _id, update) => moved(update.anchor_ms));
    render(<Page notes={[POINT]} />);

    fireEvent.pointerDown(grip("n-point"), { pointerType: "mouse", button: 0, clientX: 20, clientY: 20 });
    expect(noteLine("n-point")).toHaveClass("opacity-50");
    // Over the line's words, deep inside it.
    dragOver(within(line(3)).getByText("Next item is hiring"));
    expect(screen.getByTestId("note-drop-bar")).toBeInTheDocument();
    fireEvent.pointerUp(window);

    await waitFor(() =>
      expect(updateTimelineNote).toHaveBeenCalledWith("rec-1", "n-point", {
        expected_revision: 2,
        anchor_ms: 200_000,
      }),
    );
    expect(updateTimelineNote).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("note-drop-bar")).toBeNull();
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "line:2", "line:3", "note:n-point"]);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Note moved after the line at 03:20."));
    expect(myNotesTime("n-point")).toBe("03:20");
  });

  it("keeps its target over the margin under a line, where the drop line is, and a group's time column", async () => {
    updateTimelineNote.mockImplementation(async (_recording, _id, update) => moved(update.anchor_ms));
    render(<Page notes={[POINT]} />);

    fireEvent.pointerDown(grip("n-point"), { pointerType: "mouse", button: 0 });
    dragOver(line(2));
    // Between the rows: the line's group, then the region itself.
    dragOver(line(2).parentElement);
    dragOver(scrollRegion());
    expect(screen.getByTestId("note-drop-bar")).toBeInTheDocument();
    fireEvent.pointerUp(window);

    await waitFor(() =>
      expect(updateTimelineNote).toHaveBeenCalledWith("rec-1", "n-point", {
        expected_revision: 2,
        anchor_ms: 130_000,
      }),
    );
  });

  it("drops its target when the pointer leaves the transcript", () => {
    render(<Page notes={[POINT]} />);

    fireEvent.pointerDown(grip("n-point"), { pointerType: "mouse", button: 0 });
    dragOver(line(2));
    dragOver(screen.getByTestId("my-notes"));
    expect(screen.queryByTestId("note-drop-bar")).toBeNull();
    fireEvent.pointerUp(window);

    expect(transcriptOrder()).toEqual(["line:0", "line:1", "note:n-point", "line:2", "line:3"]);
  });

  it("drops it after a line when let go over a note already after that line", async () => {
    updateTimelineNote.mockImplementation(async (_recording, _id, update) => moved(update.anchor_ms));
    const other = note("n-other", "point", "Already here", { anchor_ms: 131_000 });
    render(<Page notes={[POINT, other]} />);

    fireEvent.pointerDown(grip("n-point"), { pointerType: "mouse", button: 0 });
    dragOver(within(noteLine("n-other")!).getByText("Already here"));
    fireEvent.pointerUp(window);

    await waitFor(() =>
      expect(updateTimelineNote).toHaveBeenCalledWith("rec-1", "n-point", {
        expected_revision: 2,
        anchor_ms: 130_000,
      }),
    );
  });

  it("stays put when let go over its own line, outside the lines, or after Escape", () => {
    render(<Page notes={[POINT]} />);

    fireEvent.pointerDown(grip("n-point"), { pointerType: "mouse", button: 0 });
    dragOver(line(1));
    expect(screen.queryByTestId("note-drop-bar")).toBeNull();
    fireEvent.pointerUp(window);

    fireEvent.pointerDown(grip("n-point"), { pointerType: "mouse", button: 0 });
    dragOver(screen.getByTestId("my-notes"));
    fireEvent.pointerUp(window);

    fireEvent.pointerDown(grip("n-point"), { pointerType: "mouse", button: 0 });
    dragOver(line(2));
    expect(screen.getByTestId("note-drop-bar")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(noteLine("n-point")).not.toHaveClass("opacity-50");
    fireEvent.pointerUp(window);

    expect(updateTimelineNote).not.toHaveBeenCalled();
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "note:n-point", "line:2", "line:3"]);
  });

  it("ends a drag whose button was let go unheard, so the next click drops nothing", () => {
    render(<Page notes={[POINT]} />);

    fireEvent.pointerDown(grip("n-point"), { pointerType: "mouse", button: 0 });
    dragOver(line(2));
    // Back from another window with the button up: the first move ends the drag.
    under = line(3);
    fireEvent.pointerMove(window, { clientX: 20, clientY: 40, buttons: 0 });
    expect(noteLine("n-point")).not.toHaveClass("opacity-50");
    expect(screen.queryByTestId("note-drop-bar")).toBeNull();
    fireEvent.pointerUp(window);

    expect(updateTimelineNote).not.toHaveBeenCalled();
    expect(transcriptOrder()).toEqual(["line:0", "line:1", "note:n-point", "line:2", "line:3"]);
  });

  it("leaves a touch to scroll: a finger on the grip starts no drag", () => {
    render(<Page notes={[POINT]} />);

    fireEvent.pointerDown(grip("n-point"), { pointerType: "touch", button: 0 });
    dragOver(line(3));
    expect(noteLine("n-point")).not.toHaveClass("opacity-50");
    expect(screen.queryByTestId("note-drop-bar")).toBeNull();
    fireEvent.pointerUp(window);

    expect(transcriptOrder()).toEqual(["line:0", "line:1", "note:n-point", "line:2", "line:3"]);
  });

  it("keeps a moved note after its line when the lines are fetched again", async () => {
    updateTimelineNote.mockImplementation(async (_recording, _id, update) => moved(update.anchor_ms));
    const { rerender } = render(<Page notes={[POINT]} />);

    fireEvent.pointerDown(grip("n-point"), { pointerType: "mouse", button: 0 });
    dragOver(line(2));
    fireEvent.pointerUp(window);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Note moved after the line at 02:10."));

    // The lines again, as new objects, one of them edited and its end moved:
    // the note stays after the line it was dropped on.
    const refreshed = SEGMENTS.map((segment) => ({ ...segment }));
    refreshed[2] = { ...refreshed[2], text: "Let's move on to hiring", end: 140 };
    rerender(<Page notes={[POINT]} segments={refreshed} />);

    expect(transcriptOrder()).toEqual(["line:0", "line:1", "line:2", "note:n-point", "line:3"]);
    expect(myNotesTime("n-point")).toBe("02:10");
  });
});
