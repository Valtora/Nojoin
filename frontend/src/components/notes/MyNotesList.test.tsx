import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptSection from "@/app/(dashboard)/recordings/[id]/_components/TranscriptSection";
import { forgetNoteOutboxes } from "@/lib/notes/noteOutboxes";
import { useNoteEdits } from "@/lib/notes/useNoteEdits";
import type { LiveTranscript } from "../transcript/_hooks/useLiveTranscript";
import {
  RecordingStatus,
  type Recording,
  type TimelineNote,
  type TranscriptSegment,
} from "@/types";

import MyNotesList from "./MyNotesList";
import RecordingMyNotes from "./RecordingMyNotes";

const updateTimelineNote = vi.fn();
const deleteTimelineNote = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  createTimelineNote: vi.fn(),
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

const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_0", start: 0, end: 2, text: "Good morning" },
  { id: "u1", speaker: "SPEAKER_1", start: 65, end: 68, text: "Forty thousand for the venue" },
  { id: "u2", speaker: "SPEAKER_0", start: 130, end: 133, text: "Let's move on" },
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

const HEAD = note("n-head", "head", "Agenda: budget, venue");
const TAIL = note("n-tail", "tail", "Follow up with finance");
const POINT = note("n-point", "point", "Ask about catering", { anchor_ms: 66_000 });
const SELECTION = note("n-sel", "selection", "Check the figure", {
  start_ms: 65_000,
  end_ms: 66_500,
  quoted_text: "Forty thousand",
});

const conflict = (current: TimelineNote) => ({
  isAxiosError: true,
  response: {
    status: 409,
    data: { detail: { code: "revision_conflict", message: "changed", note: current } },
  },
});

/** The list and the transcript sharing one set of edits, as on the recording page. */
function Page({ notes, onPlay = vi.fn(), disablePlayback = false, live = false, segments = SEGMENTS,
  showList = true, showTranscript = true }: {
  notes: TimelineNote[] | null;
  onPlay?: (s: number) => void;
  disablePlayback?: boolean;
  live?: boolean;
  segments?: TranscriptSegment[];
  showList?: boolean;
  showTranscript?: boolean;
}) {
  const noteEdits = useNoteEdits(RECORDING.id);
  const recording = live ? { ...RECORDING, status: RecordingStatus.PAUSED } : RECORDING;
  const liveTranscript: LiveTranscript | undefined = live ? {
    segments, speakers: [], hasLoaded: true, liveSpeakerNames: {},
    applyLiveSpeakerNames: vi.fn(), refresh: vi.fn(),
  } : undefined;
  return (
    <>
      {showList ? <RecordingMyNotes
        recording={recording}
        timelineNotes={notes}
        segments={segments}
        noteEdits={noteEdits}
        onPlay={onPlay}
        disablePlayback={disablePlayback}
      /> : null}
      {showTranscript ? <TranscriptSection
        active
        recording={recording}
        liveTranscript={liveTranscript}
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
      /> : null}
    </>
  );
}

const rows = () => screen.queryAllByTestId("my-note");
const row = (id: string) => rows().find((candidate) => candidate.dataset.noteId === id)!;
const noteLine = (id: string) =>
  screen.queryAllByTestId("timeline-note").find((line) => line.dataset.noteId === id);
const scrollRegion = () => screen.getByTestId("transcript-scroll-region");

const openFromList = (id: string) => {
  fireEvent.click(within(row(id)).getByRole("button", { name: /^Edit note/ }));
  return screen.getByRole("dialog");
};

beforeEach(() => {
  forgetNoteOutboxes();
  updateTimelineNote.mockReset();
  deleteTimelineNote.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("My notes", () => {
  it("asks before the page is left while an edit is unsaved, and not once it is saved", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<><a href="/people">People</a><Page notes={[HEAD]} /></>);
    const leave = () => {
      const unload = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(unload);
      return unload.defaultPrevented;
    };
    const editor = openFromList(HEAD.id);
    expect(leave()).toBe(false);
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Not saved yet" } });
    expect(leave()).toBe(true);
    // An in-app link asks too, and staying keeps the editor and its text.
    expect(fireEvent.click(screen.getByRole("link", { name: "People" }))).toBe(false);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(within(editor).getByRole("textbox")).toHaveValue("Not saved yet");

    let answer!: (note: TimelineNote) => void;
    updateTimelineNote.mockReturnValueOnce(new Promise<TimelineNote>((resolve) => { answer = resolve; }));
    fireEvent.click(within(editor).getByRole("button", { name: "Save note" }));
    // Text typed now would not be in the save on its way.
    expect(within(editor).getByRole("textbox")).toHaveAttribute("readonly");
    expect(leave()).toBe(true);
    await act(async () => answer({ ...HEAD, body: "Not saved yet", revision: 3 }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(leave()).toBe(false);
    expect(fireEvent.click(screen.getByRole("link", { name: "People" }))).toBe(true);
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("keeps an editor with unsaved text open when another note's pencil is pressed", () => {
    render(<Page notes={[HEAD, TAIL]} />);
    let editor = openFromList(HEAD.id);
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Still typing" } });
    editor = openFromList(TAIL.id);
    expect(within(editor).getByRole("textbox")).toHaveValue("Still typing");
    fireEvent.click(within(editor).getByRole("button", { name: "Cancel" }));
    // An unchanged editor closes on the press, so the next pencil opens its note.
    openFromList(TAIL.id);
    fireEvent.pointerDown(within(row(HEAD.id)).getByRole("button", { name: /^Edit note/ }));
    editor = openFromList(HEAD.id);
    expect(within(editor).getByRole("textbox")).toHaveValue(HEAD.body);
  });

  it("lets the page go when leaving with an unsaved edit is confirmed", () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<><a href="/people">People</a><Page notes={[HEAD]} /></>);
    const editor = openFromList(HEAD.id);
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Given up" } });
    expect(fireEvent.click(screen.getByRole("link", { name: "People" }))).toBe(true);
  });

  it.each([false, true])("lists each saved note once and keeps whole-meeting notes around an empty transcript, live=%s", (live) => {
    render(<Page notes={[TAIL, HEAD]} live={live} segments={[]} />);
    expect(rows().map((candidate) => candidate.dataset.noteId)).toEqual([HEAD.id, TAIL.id]);
    expect(screen.queryByText("Other notes")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    const transcriptNotes = screen.getAllByTestId("timeline-note");
    expect(transcriptNotes.map((line) => line.dataset.noteId)).toEqual([HEAD.id, TAIL.id]);
    expect(transcriptNotes[0]).toHaveTextContent("Before the transcript");
    expect(transcriptNotes[1]).toHaveTextContent("After the transcript");
  });

  it("keeps note navigation and editing while blocking playback until processing finishes", () => {
    const onPlay = vi.fn();
    const view = render(<Page notes={[POINT]} onPlay={onPlay} disablePlayback />);
    const play = within(row(POINT.id)).getByRole("button", { name: "Play from 01:06" });
    expect(play).toBeDisabled();
    fireEvent.click(play);
    expect(onPlay).not.toHaveBeenCalled();
    expect(within(row(POINT.id)).getByRole("button", { name: /^Show in the transcript/ })).toBeEnabled();
    expect(within(row(POINT.id)).getByRole("button", { name: /^Edit note/ })).toBeEnabled();

    view.rerender(<Page notes={[POINT]} onPlay={onPlay} />);
    fireEvent.click(within(row(POINT.id)).getByRole("button", { name: "Play from 01:06" }));
    expect(onPlay).toHaveBeenCalledWith(66);
  });

  it("lists notes before the transcript first, the rest by time, ties by creation, then notes after it", () => {
    const sameMomentLater = note("n-a", "point", "Second at 01:06", {
      anchor_ms: 66_000,
      created_at: "2026-10-23T10:05:00",
    });
    const early = note("n-early", "point", "Before the first line", { anchor_ms: 0 });
    const late = note("n-late", "point", "Near the end", { anchor_ms: 131_000 });
    const secondHead = note("n-head2", "head", "Also on the whole meeting", {
      created_at: "2026-10-23T10:09:00",
    });
    render(
      <Page notes={[TAIL, late, sameMomentLater, secondHead, POINT, SELECTION, early, HEAD]} />,
    );

    expect(rows().map((candidate) => candidate.dataset.noteId)).toEqual([
      "n-head",
      "n-head2",
      "n-early",
      // The selection's time is its last word's, 01:06.499, after the points at 01:06.
      "n-point",
      "n-a",
      "n-sel",
      "n-late",
      "n-tail",
    ]);
    expect(row("n-head")).toHaveTextContent("Before the transcript");
    expect(row("n-tail")).toHaveTextContent("After the transcript");
    expect(row("n-sel")).toHaveTextContent("01:05");
    expect(row("n-sel")).toHaveTextContent("“Forty thousand”");
  });

  it("says so when there are no notes, and while they load", () => {
    const { rerender } = render(<Page notes={null} />);
    expect(screen.getByText("Loading notes…")).toBeInTheDocument();

    rerender(<Page notes={[]} />);
    expect(screen.getByText(/^No notes yet\./)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "My notes" })).toBeNull();
  });

  it("marks a selection note whose words are gone, as its line does", () => {
    const gone = note("n-gone", "selection", "Ask finance", {
      start_ms: 65_000,
      end_ms: 66_500,
      quoted_text: "a budget of twelve",
    });
    render(<Page notes={[gone, SELECTION]} />);

    expect(within(row("n-gone")).getByText("Words changed since this note.")).toBeInTheDocument();
    expect(within(row("n-sel")).queryByText("Words changed since this note.")).toBeNull();
  });

  it("plays from a note's time, and offers no jump or play for a note on the whole meeting", () => {
    const onPlay = vi.fn();
    render(<Page notes={[HEAD, POINT]} onPlay={onPlay} />);

    fireEvent.click(within(row("n-point")).getByRole("button", { name: "Play from 01:06" }));
    expect(onPlay).toHaveBeenCalledWith(66);
    expect(within(row("n-head")).queryByRole("button", { name: /^Play/ })).toBeNull();
    expect(within(row("n-head")).queryByRole("button", { name: /^Show in the transcript/ })).toBeNull();
    expect(within(row("n-head")).getByRole("button", { name: /^Edit note before the transcript/ })).toBeInTheDocument();
  });

  it("shows an edit made in the list on the note's line, against the note's revision", async () => {
    updateTimelineNote.mockImplementation(async (_recording, _id, update) => ({
      ...POINT,
      body: update.body,
      revision: 3,
    }));
    render(<Page notes={[POINT]} />);

    const editor = openFromList("n-point");
    expect(editor).toHaveAccessibleName("Edit the note at 01:06");
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Ask about lunch" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save note" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(updateTimelineNote).toHaveBeenCalledWith("rec-1", "n-point", {
      expected_revision: 2,
      body: "Ask about lunch",
    });
    expect(row("n-point")).toHaveTextContent("Ask about lunch");
    expect(noteLine("n-point")).toHaveTextContent("Ask about lunch");
  });

  it("shows an edit made on the line in the list", async () => {
    updateTimelineNote.mockResolvedValue({ ...POINT, body: "Edited on the line", revision: 3 });
    render(<Page notes={[POINT]} />);

    fireEvent.click(within(noteLine("n-point")!).getByRole("button", { name: /^Edit note at 01:06/ }));
    const editor = screen.getByRole("dialog");
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Edited on the line" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save note" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(row("n-point")).toHaveTextContent("Edited on the line");
  });

  it("removes a note deleted in the list from the list and its line, and focus goes to the list", async () => {
    deleteTimelineNote.mockResolvedValue(undefined);
    render(<Page notes={[POINT, TAIL]} />);

    const editor = openFromList("n-point");
    fireEvent.click(within(editor).getByRole("button", { name: "Delete" }));
    fireEvent.click(within(editor).getByRole("button", { name: "Confirm delete" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(deleteTimelineNote).toHaveBeenCalledWith("rec-1", "n-point", { expectedRevision: 2 });
    expect(rows().map((candidate) => candidate.dataset.noteId)).toEqual(["n-tail"]);
    expect(noteLine("n-point")).toBeUndefined();
    expect(document.activeElement).toBe(screen.getByTestId("my-notes"));
  });

  it("edits a note after the transcript, which has no line, by its own name", async () => {
    updateTimelineNote.mockResolvedValue({ ...TAIL, body: "Follow up with Sam", revision: 3 });
    render(<Page notes={[TAIL]} />);

    const editor = openFromList("n-tail");
    expect(editor).toHaveAccessibleName("Edit the note after the transcript");
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Follow up with Sam" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save note" }));

    await waitFor(() => expect(row("n-tail")).toHaveTextContent("Follow up with Sam"));
  });

  it("on a change made elsewhere, shows the note as it is now and keeps the text typed", async () => {
    updateTimelineNote.mockRejectedValueOnce(conflict({ ...POINT, body: "Changed on my phone", revision: 5 }));
    render(<Page notes={[POINT]} />);

    const editor = openFromList("n-point");
    fireEvent.change(within(editor).getByRole("textbox"), { target: { value: "Mine" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save note" }));

    await within(editor).findByText(
      "This note was changed elsewhere. It now reads “Changed on my phone”. Save again to keep yours.",
    );
    expect(within(editor).getByRole("textbox")).toHaveValue("Mine");
    expect(row("n-point")).toHaveTextContent("Changed on my phone");
    expect(noteLine("n-point")).toHaveTextContent("Changed on my phone");
  });

  describe("a click on a note", () => {
    const place = (element: Element, top: number, height: number) =>
      vi.spyOn(element, "getBoundingClientRect").mockReturnValue({ top, height } as DOMRect);

    const stageRegion = () => {
      const region = scrollRegion();
      place(region, 100, 400);
      Object.defineProperty(region, "clientHeight", { configurable: true, value: 400 });
      Object.defineProperty(region, "scrollTop", { configurable: true, value: 50 });
      region.scrollTo = vi.fn();
      return region;
    };

    it("scrolls the transcript, not the page, to centre the note's line, and tells the page", () => {
      const onShowTranscript = vi.fn();
      function WithJump() {
        const noteEdits = useNoteEdits(RECORDING.id);
        return (
          <>
            <MyNotesList
              recordingId={RECORDING.id}
              timelineNotes={[POINT]}
              segments={SEGMENTS}
              noteEdits={noteEdits}
              onPlay={vi.fn()}
              onShowTranscript={onShowTranscript}
            />
            <div id="transcript-view">
              <div data-testid="transcript-scroll-region">
                <div data-order-index="1" />
                <div data-testid="timeline-note" data-note-id="n-point" />
              </div>
            </div>
          </>
        );
      }
      render(<WithJump />);
      const region = stageRegion();
      place(screen.getAllByTestId("timeline-note")[0], 900, 40);

      fireEvent.click(within(row("n-point")).getByRole("button", { name: /^Show in the transcript: the note at 01:06/ }));

      expect(onShowTranscript).toHaveBeenCalledTimes(1);
      // 50 scrolled + 800 below the region's top, less half of what's left of the region's height.
      expect(region.scrollTo).toHaveBeenCalledWith({ top: 50 + 800 - (400 - 40) / 2, behavior: "smooth" });
    });

    it("goes to the line a note follows when its line is folded behind a count", () => {
      const second = note("n-point2", "point", "And drinks", { anchor_ms: 67_000 });
      render(<Page notes={[POINT, second]} />);
      expect(noteLine("n-point")).toBeUndefined();
      const region = stageRegion();
      const line = region.querySelector('[data-order-index="1"]')!;
      place(line, 300, 60);

      fireEvent.click(within(row("n-point")).getByRole("button", { name: /^Show in the transcript/ }));

      expect(region.scrollTo).toHaveBeenCalledWith({ top: 50 + 200 - (400 - 60) / 2, behavior: "smooth" });
    });

    it("goes to a folded selection note's marked words", () => {
      const second = note("n-point2", "point", "And drinks", { anchor_ms: 66_600 });
      render(<Page notes={[SELECTION, second]} />);
      expect(noteLine("n-sel")).toBeUndefined();
      const region = stageRegion();
      const mark = region.querySelector('mark[data-note-id="n-sel"]')!;
      place(mark, 260, 20);

      fireEvent.click(within(row("n-sel")).getByRole("button", { name: /^Show in the transcript/ }));

      expect(region.scrollTo).toHaveBeenCalledWith({ top: 50 + 160 - (400 - 20) / 2, behavior: "smooth" });
    });

    it("goes to the top for a note before every line", () => {
      const early = note("n-early", "point", "Before anyone spoke", { anchor_ms: 0 });
      const segments = SEGMENTS.map((segment) => ({ ...segment, start: segment.start + 5, end: segment.end + 5 }));
      function Early() {
        const noteEdits = useNoteEdits(RECORDING.id);
        return (
          <>
            <MyNotesList
              recordingId={RECORDING.id}
              timelineNotes={[early]}
              segments={segments}
              noteEdits={noteEdits}
              onPlay={vi.fn()}
            />
            <div id="transcript-view">
              <div data-testid="transcript-scroll-region">
                <div data-order-index="0" />
              </div>
            </div>
          </>
        );
      }
      render(<Early />);
      const region = stageRegion();

      fireEvent.click(within(row("n-early")).getByRole("button", { name: /^Show in the transcript/ }));

      expect(region.scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "smooth" });
    });
  });
});
