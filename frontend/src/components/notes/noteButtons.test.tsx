import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EMPTY_DRAFT, type OutboxEntry } from "@/lib/notes/noteOutbox";
import { forgetNoteOutboxes, noteOutboxFor } from "@/lib/notes/noteOutboxes";

import LiveNoteComposer from "./LiveNoteComposer";
import RecordingUnsavedNotes from "./RecordingUnsavedNotes";
import TimelineNoteLine from "./TimelineNoteLine";
import UnsavedNotesList from "./UnsavedNotesList";

// The design system's focus ring, which the Button and IconButton primitives
// carry (docs/DESIGN.md, "The flat canon").
const FOCUS_RING = "focus-visible:outline-focus-ring";

const failed: OutboxEntry = {
  key: "n1",
  id: "n1",
  writeToken: "t1",
  kind: "point",
  body: "Budget agreed",
  anchorMs: 61_000,
  createdAt: "2026-10-21T10:00:00Z",
  status: "failed",
  error: "Not saved: no answer from Nojoin.",
};

const expectEveryButtonRinged = (minimum: number) => {
  const buttons = screen.getAllByRole("button");
  expect(buttons.length).toBeGreaterThanOrEqual(minimum);
  for (const button of buttons) expect(button.className).toContain(FOCUS_RING);
};

describe("note buttons", () => {
  afterEach(() => forgetNoteOutboxes());

  it("give the note box's Send the focus ring", () => {
    render(<LiveNoteComposer readAudioNow={() => 0} onSubmit={vi.fn(() => null)} />);
    expectEveryButtonRinged(1);
  });

  it("give a failed note's Retry and Discard the focus ring, and a touch target", () => {
    render(
      <TimelineNoteLine
        noteId="n1"
        body="Budget agreed"
        timeLabel="01:01"
        status="failed"
        onRetry={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );
    expectEveryButtonRinged(2);
    for (const button of screen.getAllByRole("button")) {
      expect(button.className).toContain("pointer-coarse:h-[44px]");
    }
  });

  it("give the unsaved list's buttons the focus ring", () => {
    render(<UnsavedNotesList entries={[failed]} onRetry={vi.fn()} onDiscard={vi.fn()} />);
    expectEveryButtonRinged(2);
  });

  it("give a draft left after Stop its buttons with the focus ring", () => {
    noteOutboxFor("rec-1").setDraft({ ...EMPTY_DRAFT, text: "Half a thought" });
    render(<RecordingUnsavedNotes recordingId="rec-1" readEndMs={() => 0} />);
    expectEveryButtonRinged(2);
  });
});
