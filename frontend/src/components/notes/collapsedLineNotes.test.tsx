import { fireEvent, render, screen } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/components/LiveTranscriptPanel";
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

function renderFinished(timelineNotes: TimelineNote[]) {
  render(
    <TranscriptView
      recordingId="rec-1"
      segments={SEGMENTS}
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
    />,
  );
}

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

  it("puts no chip on a line without notes", () => {
    renderFinished([saved("n1", "Check the figure", 13_000)]);
    expect(screen.getAllByTestId("line-notes-chip")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "1 note on the line at 00:12" })).toBeInTheDocument();
  });

  it("never folds a note still sending or refused, and counts only the saved ones", () => {
    const [first, second] = TWO_ON_THE_VENUE;
    const sending = saved("n3", "Still on its way", 15_000);
    render(
      <LiveTranscriptPanel
        segments={SEGMENTS}
        hasLoaded
        notes={[inline(first), inline(second, "failed"), inline(sending, "saving")]}
        onRetryNote={vi.fn()}
        onDiscardNote={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "1 note on the line at 00:12" })).toBeInTheDocument();
    expect(notesShown()).toHaveLength(2);
    expect(notesShown().join(" ")).toContain("Ask about catering");
    expect(notesShown().join(" ")).toContain("Still on its way");
    expect(screen.getByRole("button", { name: /^Retry note/ })).toBeInTheDocument();
  });

  it("puts no chip on notes that are all unsaved", () => {
    const [first] = TWO_ON_THE_VENUE;
    render(<LiveTranscriptPanel segments={SEGMENTS} hasLoaded notes={[inline(first, "saving")]} />);
    expect(screen.queryByTestId("line-notes-chip")).not.toBeInTheDocument();
    expect(notesShown()).toHaveLength(1);
  });

  it("keeps a note written here in full once it saves", () => {
    const [first] = TWO_ON_THE_VENUE;
    const { rerender } = render(
      <LiveTranscriptPanel segments={SEGMENTS} hasLoaded notes={[inline(first, "saving")]} />,
    );
    rerender(<LiveTranscriptPanel segments={SEGMENTS} hasLoaded notes={[inline(first)]} />);

    expect(screen.queryByTestId("line-notes-chip")).not.toBeInTheDocument();
    expect(notesShown()).toHaveLength(1);
  });

  it("is an item of the live view's list, so the list stays a list", () => {
    render(<LiveTranscriptPanel segments={SEGMENTS} hasLoaded notes={TWO_ON_THE_VENUE.map((note) => inline(note))} />);
    const chip = screen.getByTestId("line-notes-chip");
    expect(chip.tagName).toBe("LI");
    expect(chip.parentElement?.tagName).toBe("OL");
  });
});
