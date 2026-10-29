import { createRef, type ComponentProps } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptSection from "@/test/TranscriptSectionWithEdits";
import { forgetNoteOutboxes } from "@/lib/notes/noteOutboxes";
import { ClientStatus, RecordingStatus, type Recording, type TimelineNote, type TranscriptSegment } from "@/types";
import RecordingNoteComposer from "./RecordingNoteComposer";

const createNote = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(), createTimelineNote: createNote,
}));
vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));
vi.mock("@/lib/captures/useCaptureActions", () => ({
  CAPTURE_NOTE_BODY: "Screen capture", captureShortcutLabel: () => "",
  useCaptureActions: () => ({ canCaptureScreen: false, busy: false, message: null }),
}));

const line: TranscriptSegment = { id: "u1", speaker: "SPEAKER_0", start: 0, end: 3, text: "The budget" };
const first: TimelineNote = {
  id: "n1", recording_id: "rec-1", kind: "point", body: "First thought", anchor_ms: 1_000, revision: 1,
  created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
};
type Source = "composer" | "live menu" | "settled menu";

function Page({ source, settled = false, notes = [first] }: {
  source: Source; settled?: boolean; notes?: TimelineNote[];
}) {
  const live = source !== "settled menu" && !settled;
  const recording = { id: "rec-1", speakers: [], duration_seconds: 3,
    status: live ? RecordingStatus.PAUSED : RecordingStatus.PROCESSED,
    client_status: live ? ClientStatus.PAUSED : undefined } as unknown as Recording;
  const boxRef = createRef<HTMLTextAreaElement>();
  const props: ComponentProps<typeof TranscriptSection> = {
    active: true, recording, timelineNotes: notes, transcriptSegments: [line], currentTime: -1, isPlaying: false,
    speakerMap: { SPEAKER_0: "Alex" }, speakerColors: {}, globalSpeakers: [], canUndo: false, canRedo: false,
    deferredTranscriptUtteranceIds: [], onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(),
    onRenameSpeaker: vi.fn(), onRefresh: vi.fn(), onUpdateSegmentSpeaker: vi.fn(), onUpdateSegmentText: vi.fn(),
    onFindAndReplace: vi.fn(), onUndo: vi.fn(), onRedo: vi.fn(), onExport: vi.fn(),
    onActiveEditUtteranceChange: vi.fn(), liveNoteBoxRef: boxRef,
    liveTranscript: live ? { segments: [line], speakers: [], hasLoaded: true,
      liveSpeakerNames: {}, refresh: vi.fn(), applyLiveSpeakerNames: vi.fn() } : undefined,
    inFlightContent: <RecordingNoteComposer recording={recording} segments={[line]} boxRef={boxRef} />,
  };
  return <TranscriptSection {...props} />;
}

const deferred = () => {
  let resolve!: (note: TimelineNote) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<TimelineNote>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};

function submit(source: Source) {
  if (source !== "composer") {
    fireEvent.click(screen.getByRole("button", { name: /Actions for .*line at 00:00/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
  }
  const box = screen.getByRole("textbox", { name: source === "settled menu" ? "Note" : "New note" });
  fireEvent.change(box, { target: { value: "Second thought" } });
  fireEvent.keyDown(box, { key: "Enter" });
}

const originalScroll = Element.prototype.scrollIntoView;
beforeAll(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterAll(() => { Element.prototype.scrollIntoView = originalScroll; });
beforeEach(() => { forgetNoteOutboxes(); createNote.mockReset(); });

describe("line note collapse through the recording outbox", () => {
  it.each(["composer", "live menu", "settled menu"] as const)(
    "folds a second note from the %s before its request answers", async (source) => {
      const request = deferred();
      createNote.mockReturnValueOnce(request.promise);
      const view = render(<Page source={source} />);
      expect(screen.getByTestId("timeline-note")).toHaveTextContent("First thought");
      submit(source);
      const chip = screen.getByRole("button", { name: "2 notes on the line at 00:00, saving" });
      expect(chip).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryAllByTestId("timeline-note")).toHaveLength(0);
      await waitFor(() => expect(createNote).toHaveBeenCalledOnce());
      const payload = createNote.mock.calls[0][1];
      await act(async () => request.resolve({ ...first, ...payload, revision: 1 }));
      expect(screen.getByRole("button", { name: "2 notes on the line at 00:00" })).toHaveAttribute(
        "aria-expanded", "false",
      );
      fireEvent.click(screen.getByRole("button", { name: "2 notes on the line at 00:00" }));
      expect(screen.getAllByTestId("timeline-note")).toHaveLength(2);
      view.rerender(<Page source={source} settled />);
      expect(screen.getByRole("button", { name: "2 notes on the line at 00:00" })).toHaveAttribute(
        "aria-expanded", "true",
      );
      expect(screen.getAllByTestId("timeline-note")).toHaveLength(2);
    },
  );

  it("exposes a failed third note, then folds the retry without hiding recovery permanently", async () => {
    const request = deferred();
    createNote.mockReturnValueOnce(request.promise);
    const notes = [first, { ...first, id: "n2", body: "Another saved thought" }];
    render(<Page source="composer" notes={notes} />);
    submit("composer");
    expect(screen.getByRole("button", { name: "3 notes on the line at 00:00, saving" })).toBeInTheDocument();
    await act(async () => request.reject(new Error("Network error")));
    const failed = await screen.findByTestId("timeline-note");
    expect(failed).toHaveTextContent("Second thought");
    const retry = within(failed).getByRole("button", { name: /^Retry note/ });
    expect(within(failed).getByRole("button", { name: /^Discard note/ })).toBeInTheDocument();
    const secondRequest = deferred();
    createNote.mockReturnValueOnce(secondRequest.promise);
    retry.focus();
    fireEvent.click(retry);
    expect(screen.getByRole("button", { name: "3 notes on the line at 00:00, saving" })).toHaveFocus();
    expect(screen.queryByTestId("timeline-note")).not.toBeInTheDocument();
    await act(async () => secondRequest.reject(new Error("Network error")));
    expect(await screen.findByRole("button", { name: /^Retry note/ })).toBeInTheDocument();
  });
});
