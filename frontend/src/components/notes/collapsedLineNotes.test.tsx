import { fireEvent, render, screen } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/test/SharedLiveTranscript";
import TranscriptView from "@/components/TranscriptView";
import type { InlineNote } from "@/lib/notes/noteOutbox";
import type { TimelineNote, TranscriptSegment } from "@/types";

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
  { id: "u0", speaker: "SPEAKER_0", start: 1, end: 3, text: "Good morning" },
  { id: "u1", speaker: "SPEAKER_0", start: 12, end: 15.5, text: "Forty thousand for the venue" },
];

const saved = (id: string, body: string, atMs: number): TimelineNote => ({
  id,
  recording_id: "rec-1",
  kind: "point",
  body,
  anchor_ms: atMs,
  start_ms: null,
  end_ms: null,
  quoted_text: null,
  created_at: "2026-10-23T10:00:00Z",
  updated_at: "2026-10-23T10:00:00Z",
  revision: 1,
});

const inline = (note: TimelineNote, status: InlineNote["status"] = "saved"): InlineNote => ({
  ...note,
  kind: "point",
  key: note.id,
  status,
  error: status === "failed" ? "Not saved: no answer from Nojoin." : null,
});

const TWO_ON_THE_VENUE = [saved("n1", "Check the figure", 13_000), saved("n2", "Ask about catering", 14_000)];

const notesShown = () => screen.queryAllByTestId("timeline-note").map((note) => note.textContent);

function Finished({ timelineNotes, segments = SEGMENTS }: {
  timelineNotes: (TimelineNote | InlineNote)[];
  segments?: TranscriptSegment[];
}) {
  return (
    <TranscriptView
      recordingId="rec-1"
      segments={segments}
      timelineNotes={timelineNotes}
      currentTime={-1}
      onPlaySegment={vi.fn()}
      isPlaying={false}
      onPause={vi.fn()}
      onResume={vi.fn()}
      speakerMap={{ SPEAKER_0: "Alex" }}
      speakers={[]}
      globalSpeakers={[]}
      onRenameSpeaker={vi.fn()}
      onUpdateSegmentSpeaker={vi.fn()}
      onUpdateSegmentText={vi.fn()}
      onFindAndReplace={vi.fn()}
      speakerColors={{ SPEAKER_0: "blue" }}
      onUndo={vi.fn()}
      onRedo={vi.fn()}
      canUndo={false}
      canRedo={false}
      onExport={vi.fn()}
    />
  );
}

const renderFinished = (timelineNotes: TimelineNote[]) => render(<Finished timelineNotes={timelineNotes} />);

describe("a line's notes, folded", () => {
  it("shows a count in place of the notes, which a press on it shows and hides", () => {
    renderFinished(TWO_ON_THE_VENUE);
    const chip = screen.getByRole("button", { name: "2 notes on the line at 00:12" });
    expect(chip).toHaveAttribute("aria-expanded", "false");
    expect(chip).toHaveTextContent("2 notes");
    expect(notesShown()).toEqual([]);

    fireEvent.click(chip);
    expect(chip).toHaveAttribute("aria-expanded", "true");
    expect(notesShown()).toHaveLength(2);
    expect(notesShown()[0]).toContain("Check the figure");

    fireEvent.click(chip);
    expect(notesShown()).toEqual([]);
  });

  it("shows a line's single note as it is, with no chip", () => {
    renderFinished([saved("n1", "Check the figure", 13_000)]);
    expect(screen.queryByTestId("line-notes-chip")).not.toBeInTheDocument();
    expect(notesShown()).toHaveLength(1);
    expect(notesShown()[0]).toContain("Check the figure");
  });

  it("puts no chip on a line without notes", () => {
    renderFinished(TWO_ON_THE_VENUE);
    expect(screen.getAllByTestId("line-notes-chip")).toHaveLength(1);
  });

  it("folds sending notes straight away and keeps refused notes recoverable", () => {
    const refused = saved("n3", "Refused by the server", 14_500);
    const sending = saved("n4", "Still on its way", 15_000);
    render(
      <LiveTranscriptPanel
        segments={SEGMENTS}
        hasLoaded
        notes={[...TWO_ON_THE_VENUE.map((note) => inline(note)), inline(refused, "failed"), inline(sending, "saving")]}
        onRetryNote={vi.fn()}
        onDiscardNote={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "3 notes on the line at 00:12, saving" })).toHaveTextContent("Saving…");
    expect(notesShown()).toHaveLength(1);
    expect(notesShown().join(" ")).toContain("Refused by the server");
    expect(notesShown().join(" ")).not.toContain("Still on its way");
    expect(screen.getByRole("button", { name: /^Retry note/ })).toBeInTheDocument();
  });

  it("folds a pair of sending notes before either request is answered", () => {
    render(
      <LiveTranscriptPanel
        segments={SEGMENTS}
        hasLoaded
        notes={TWO_ON_THE_VENUE.map((note) => inline(note, "saving"))}
      />,
    );
    expect(screen.getByRole("button", { name: "2 notes on the line at 00:12, saving" })).toBeInTheDocument();
    expect(notesShown()).toHaveLength(0);
  });

  it("folds a second note immediately and keeps it folded once it saves", () => {
    const writtenHere = saved("n0", "Written on this page", 12_500);
    const { rerender } = render(<Finished timelineNotes={[TWO_ON_THE_VENUE[0]]} />);
    rerender(<Finished timelineNotes={[TWO_ON_THE_VENUE[0], inline(writtenHere, "saving")]} />);
    expect(screen.getByRole("button", { name: "2 notes on the line at 00:12, saving" })).toHaveAttribute(
      "aria-expanded", "false",
    );
    expect(notesShown()).toHaveLength(0);
    rerender(
      <Finished timelineNotes={[TWO_ON_THE_VENUE[0], inline(writtenHere)]} />,
    );

    expect(screen.getByRole("button", { name: "2 notes on the line at 00:12" })).toBeInTheDocument();
    expect(notesShown()).toHaveLength(0);
  });

  it("preserves an explicit expand choice through additions, failures and a single-note interval", () => {
    const { rerender } = render(<Finished timelineNotes={TWO_ON_THE_VENUE} />);
    fireEvent.click(screen.getByRole("button", { name: "2 notes on the line at 00:12" }));
    const third = inline(saved("n3", "Third thought", 14_500), "saving");
    rerender(<Finished timelineNotes={[...TWO_ON_THE_VENUE, third]} />);
    expect(screen.getByRole("button", { name: "3 notes on the line at 00:12, saving" })).toHaveAttribute(
      "aria-expanded", "true",
    );
    expect(notesShown()).toHaveLength(3);
    rerender(<Finished timelineNotes={[TWO_ON_THE_VENUE[0]]} />);
    rerender(<Finished timelineNotes={TWO_ON_THE_VENUE} />);
    expect(screen.getByRole("button", { name: "2 notes on the line at 00:12" })).toHaveAttribute("aria-expanded", "true");
  });

  it("retains expansion when a note splits an earlier overlapping group member", () => {
    const overlapping = SEGMENTS.map((segment, index) => index === 0 ? { ...segment, end: 14 } : segment);
    const { rerender } = render(<Finished timelineNotes={TWO_ON_THE_VENUE} segments={overlapping} />);
    fireEvent.click(screen.getByRole("button", { name: "2 notes on the line at 00:12" }));
    rerender(<Finished timelineNotes={[saved("early", "Earlier thought", 2_000), ...TWO_ON_THE_VENUE]}
      segments={overlapping} />);
    expect(screen.getByRole("button", { name: "2 notes on the line at 00:12" })).toHaveAttribute("aria-expanded", "true");
    expect(notesShown()).toHaveLength(3);
  });

  it("is an item of the live view's list, so the list stays a list", () => {
    render(<LiveTranscriptPanel segments={SEGMENTS} hasLoaded notes={TWO_ON_THE_VENUE.map((note) => inline(note))} />);
    const chip = screen.getByTestId("line-notes-chip");
    expect(chip.tagName).toBe("LI");
    expect(chip.parentElement?.tagName).toBe("OL");
  });
});
