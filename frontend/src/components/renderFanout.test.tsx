import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import TranscriptView from "./TranscriptView";
import SpeakerPanel from "./SpeakerPanel";
import TranscriptSection from "@/app/(dashboard)/recordings/[id]/_components/TranscriptSection";
import { NoteFilesProvider } from "./notes/NoteFiles";
import RecordingNoteComposer from "./notes/RecordingNoteComposer";
import MyNotesList from "./notes/MyNotesList";
import CapturesGallery from "./notes/CapturesGallery";
import { forgetNoteOutboxes } from "@/lib/notes/noteOutboxes";
import { useNoteEdits } from "@/lib/notes/useNoteEdits";
import { useRecordingNotes } from "@/lib/notes/useNoteOutbox";
import { CaptureProvider } from "@/lib/capture/CaptureProvider";
import { renderCounter } from "@/test/renderCounts";
import { line, panelProps, viewProps } from "./transcript/transcriptTestFixtures";
import { RecordingStatus, type Recording, type TimelineNote } from "@/types";

// Network only; all component, note-store and persistence code runs unchanged.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  getGlobalSpeakers: vi.fn(async () => []),
  getMergedRecordingSpeakers: vi.fn(async () => []),
  getUnfinishedCaptures: vi.fn(async () => []),
  listTimelineNotes: vi.fn(async () => notes),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/recordings/r1",
  useSearchParams: () => new URLSearchParams(),
}));

const segments = Array.from({ length: 200 }, (_, i) => line({
  id: `line-${i}`, speaker: i % 2 ? "B" : "A", start: i * 4, end: i * 4 + 3, text: `Speech ${i}`,
}));
const notes: TimelineNote[] = segments.slice(0, 30).map((segment, i) => ({
  id: `note-${i}`, recording_id: "r1", kind: "point", body: `Note ${i}`, revision: 1,
  anchor_ms: segment.start * 1000, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
}));
const recording = { id: "r1", status: RecordingStatus.PROCESSED, duration_seconds: 800,
  speakers: [], transcript: { transcript_status: "completed" } } as unknown as Recording;

afterEach(() => forgetNoteOutboxes());

it("executes no empty note wrappers, or populated note rows, on a playback tick", async () => {
  const counter = await renderCounter();
  try {
    const props = viewProps(segments);
    const { rerender } = render(<TranscriptView {...props} />);
    const emptyMount = await counter.take();
    expect(emptyMount("components/notes/CollapsedLineNotes.tsx", "CollapsedLineNotes")).toBe(0);
    rerender(<TranscriptView {...props} currentTime={-0.5} />);
    const emptyTick = await counter.take();
    expect(emptyTick("components/notes/CollapsedLineNotes.tsx", "CollapsedLineNotes")).toBe(0);

    rerender(<TranscriptView {...props} timelineNotes={notes} />);
    const populated = await counter.take();
    expect(populated("components/notes/CollapsedLineNotes.tsx", "CollapsedLineNotes")).toBe(30);
    expect(populated("components/notes/TimelineNoteLine.tsx", "TimelineNoteLine")).toBe(30);
    // The page rebuilds its callbacks on playback; those must not invalidate notes.
    rerender(<TranscriptView {...props} timelineNotes={notes} currentTime={-0.5}
      onPlaySegment={() => {}} onEditNote={async () => null} />);
    await counter.take();
    rerender(<TranscriptView {...props} timelineNotes={notes} currentTime={-0.25}
      onPlaySegment={() => {}} onEditNote={async () => null} />);
    const tick = await counter.take();
    expect(tick("components/notes/CollapsedLineNotes.tsx", "CollapsedLineNotes")).toBe(0);
    expect(tick("components/notes/TimelineNoteLine.tsx", "TimelineNoteLine")).toBe(0);
  } finally { await counter.stop(); }
});

it("a note-box keystroke executes no file components or transcript lines", async () => {
  const counter = await renderCounter();
  try {
    const props = viewProps(segments);
    function Page({ listed }: { listed?: TimelineNote[] }) {
      const fetched = useRecordingNotes(recording, false);
      const shown = listed ?? fetched ?? notes;
      const noteEdits = useNoteEdits(recording.id);
      return <NoteFilesProvider recordingId={recording.id} notes={shown} noteEdits={noteEdits}>
        <TranscriptSection {...props} recording={recording} active timelineNotes={shown} inFlightContent={<span />}
          transcriptSegments={segments} noteEdits={noteEdits} deferredTranscriptUtteranceIds={[]}
          onRefresh={() => {}} onActiveEditUtteranceChange={() => {}} />
        <MyNotesList recordingId={recording.id} timelineNotes={shown} segments={segments}
          noteEdits={noteEdits} onPlay={props.onPlaySegment} />
        <CapturesGallery recordingId={recording.id} timelineNotes={shown} segments={segments} noteEdits={noteEdits} />
        <RecordingNoteComposer recording={recording} segments={segments} />
      </NoteFilesProvider>;
    }
    const { rerender } = render(<CaptureProvider><Page /></CaptureProvider>);
    await act(async () => {});
    const mount = await counter.take();
    // Each of these drew on mount, so a zero after the keystroke is a real one.
    const readers = [
      ["components/notes/NoteFiles.tsx", "NoteFilesProvider"],
      ["components/transcript/TranscriptLine.tsx", "TranscriptLine"],
      ["app/(dashboard)/recordings/[id]/_components/TranscriptSection.tsx", "TranscriptSection"],
      ["components/notes/TimelineNoteLine.tsx", "TimelineNoteLine"],
      ["components/notes/MyNotesList.tsx", "MyNotesList"],
      ["components/notes/CapturesGallery.tsx", "CapturesGallery"],
      ["components/notes/RecordingUnsavedNotes.tsx", "RecordingUnsavedNotes"],
    ] as const;
    for (const [file, name] of readers) expect(mount(file, name), name).toBeGreaterThan(0);
    expect(mount("components/transcript/TranscriptLine.tsx", "TranscriptLine")).toBe(200);
    expect(mount("components/notes/NoteFiles.tsx", "NoteFile")).toBe(0);
    fireEvent.change(screen.getByRole("textbox", { name: /note/i }), { target: { value: "a" } });
    await act(async () => {});
    const typing = await counter.take();
    expect(typing("components/notes/RecordingNoteComposer.tsx", "RecordingNoteComposer")).toBeGreaterThan(0);
    for (const [file, name] of readers) expect(typing(file, name), name).toBe(0);
    expect(typing("components/notes/NoteFiles.tsx", "NoteFile")).toBe(0);
    expect(screen.getByRole("textbox", { name: /note/i })).toHaveValue("a");
    const leave = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(leave);
    expect(leave.defaultPrevented).toBe(true);

    const withFile = notes.map((note, index): TimelineNote => index ? note : { ...note, attachment: {
      id: 7, title: "Agenda.pdf", file_type: "application/pdf", status: "PENDING",
      use_in_notes: false, readable: true, is_image: false,
    } });
    rerender(<CaptureProvider><Page listed={withFile} /></CaptureProvider>);
    const attached = await counter.take();
    expect(attached("components/notes/NoteFiles.tsx", "NoteFile")).toBe(2); // Transcript and My notes; the other 29 mount none.
    fireEvent.change(screen.getByRole("textbox", { name: /note/i }), { target: { value: "ab" } });
    const nextKey = await counter.take();
    expect(nextKey("components/notes/NoteFiles.tsx", "NoteFile")).toBe(0);
    expect(nextKey("components/transcript/TranscriptLine.tsx", "TranscriptLine")).toBe(0);
  } finally { await counter.stop(); }
});

it("changing the last live line executes only the overlap indicators whose overlap changed", async () => {
  const counter = await renderCounter();
  try {
    // Every tenth line runs a second into the next speaker's: 40 lines with crosstalk.
    const crosstalk = segments.map((segment, i) => i % 10 ? segment : { ...segment, end: segment.end + 2 });
    const props = viewProps(crosstalk);
    const live = { hasLoaded: true };
    const { rerender } = render(<TranscriptView {...props} live={live} />);
    const mount = await counter.take();
    expect(mount("components/transcript/OverlapIndicator.tsx", "OverlapIndicator")).toBe(200);
    expect(screen.getAllByRole("button", { name: "Overlap" })).toHaveLength(40);

    const revised = [...crosstalk.slice(0, -1), { ...crosstalk.at(-1)!, text: "More live words" }];
    rerender(<TranscriptView {...props} live={live} segments={revised} />);
    const delta = await counter.take();
    expect(delta("components/transcript/OverlapIndicator.tsx", "OverlapIndicator")).toBe(1);
    expect(delta("components/transcript/TranscriptLine.tsx", "TranscriptLine")).toBe(1);
    expect(screen.getByText("More live words")).toBeInTheDocument();

    // The last line now starts inside the one before it: those two lines' markers change.
    const overlapping = [...revised.slice(0, -1), { ...revised.at(-1)!, start: revised.at(-2)!.end - 1 }];
    rerender(<TranscriptView {...props} live={live} segments={overlapping} />);
    const grown = await counter.take();
    expect(grown("components/transcript/OverlapIndicator.tsx", "OverlapIndicator")).toBe(2);
    expect(screen.getAllByRole("button", { name: "Overlap" })).toHaveLength(42);
  } finally { await counter.stop(); }
});

it("a new live name proposal executes only its speaker's latest line", async () => {
  const counter = await renderCounter();
  try {
    const live = segments.map((segment, i) => ({
      ...segment, provisional: true, speaker: i % 2 ? "LIVE_PROVISIONAL_2" : "LIVE_PROVISIONAL_1",
    }));
    const props = viewProps(live);
    const handlers = { onRenameLiveSpeaker: vi.fn(), onDismissLiveSpeakerSuggestion: vi.fn() };
    const { rerender } = render(<TranscriptView {...props} live={{ hasLoaded: true, ...handlers,
      liveSpeakerSuggestions: {}, liveSpeakerSuggestionSources: {} }} />);
    await counter.take();

    rerender(<TranscriptView {...props} live={{ hasLoaded: true, ...handlers,
      liveSpeakerSuggestions: { LIVE_PROVISIONAL_1: "Alex" },
      liveSpeakerSuggestionSources: { LIVE_PROVISIONAL_1: "heuristic" } }} />);
    const proposed = await counter.take();
    expect(proposed("components/transcript/TranscriptLine.tsx", "TranscriptLine")).toBe(1);
    expect(screen.getAllByText("Alex?")).toHaveLength(1);
  } finally { await counter.stop(); }
});

it("a proposal heard on an earlier line executes only that line, and later lines leave it there", async () => {
  const counter = await renderCounter();
  try {
    const live = segments.map((segment, i) => ({
      ...segment, provisional: true, speaker: i % 2 ? "LIVE_PROVISIONAL_2" : "LIVE_PROVISIONAL_1",
    }));
    const props = viewProps(live);
    const handlers = { onRenameLiveSpeaker: vi.fn(), onDismissLiveSpeakerSuggestion: vi.fn() };
    const proposal = { liveSpeakerSuggestions: { LIVE_PROVISIONAL_1: "Alex" },
      liveSpeakerSuggestionSources: { LIVE_PROVISIONAL_1: "heuristic" as const } };
    const { rerender } = render(<TranscriptView {...props} live={{ hasLoaded: true, ...handlers,
      liveSpeakerSuggestions: {}, liveSpeakerSuggestionSources: {}, liveSpeakerSuggestionStarts: {} }} />);
    await counter.take();

    // Heard at line 50's start (200 s), far from the speaker's last line.
    rerender(<TranscriptView {...props} live={{ hasLoaded: true, ...handlers, ...proposal,
      liveSpeakerSuggestionStarts: { LIVE_PROVISIONAL_1: 200 } }} />);
    const proposed = await counter.take();
    expect(proposed("components/transcript/TranscriptLine.tsx", "TranscriptLine")).toBe(1);
    const offered = screen.getByText("Alex?").closest("[data-order-index]");
    expect(offered).toHaveAttribute("data-segment-id", "line-50");

    // The speaker says more: the proposal stays on line 50, and only the new line draws.
    const more = [...live, { ...live[198], id: "line-200", start: 800, end: 803, text: "More" }];
    rerender(<TranscriptView {...viewProps(more)} live={{ hasLoaded: true, ...handlers, ...proposal,
      liveSpeakerSuggestionStarts: { LIVE_PROVISIONAL_1: 200 } }} />);
    const added = await counter.take();
    expect(added("components/transcript/TranscriptLine.tsx", "TranscriptLine")).toBe(1);
    expect(screen.getByText("Alex?").closest("[data-order-index]")).toHaveAttribute("data-segment-id", "line-50");
  } finally { await counter.stop(); }
});

it("a new live name proposal executes only its speaker's card in the speaker panel", async () => {
  const counter = await renderCounter();
  try {
    const labels = Array.from({ length: 40 }, (_, i) => `LIVE_PROVISIONAL_${i + 1}`);
    const live = labels.map((label, i) => line({ id: `live-${i}`, speaker: label, start: i, end: i + 1 }));
    const base = { ...panelProps(), speakers: [], segments: live, onRenameLiveSpeaker: vi.fn(),
      onDismissLiveSpeakerSuggestion: vi.fn(), liveSpeakerSuggestionSources: {} };
    // The page hands over a new transcript switch on every render.
    const { rerender } = render(<SpeakerPanel {...base} liveSpeakerSuggestions={{}} onShowTranscript={() => {}} />);
    await counter.take();

    rerender(<SpeakerPanel {...base} liveSpeakerSuggestions={{ LIVE_PROVISIONAL_7: "Alex" }}
      onShowTranscript={() => {}} />);
    const proposed = await counter.take();
    expect(proposed("components/speakers/LiveSpeakerRows.tsx", "LiveSpeakerRow")).toBe(1);
    expect(screen.getAllByText("Alex?")).toHaveLength(1);
  } finally { await counter.stop(); }
});
